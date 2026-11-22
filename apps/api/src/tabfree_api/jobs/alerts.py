"""Data-driven alerts (BUILD_PLAN P6.4): a threshold on a governed query, notified without repeats.

The alert's query runs as its **owner** (their row-level security), on a schedule or after a model
refresh. The condition compares the measure column with a threshold, over any row, all rows, or the
column's sum, minimum or maximum. State (``firing``, ``last_value``, ``last_notified_at``) stops the
same alert repeating: by default it notifies when the condition becomes true and again only after it
has cleared; ``every_check`` and ``daily`` are the alternatives. Recipients must be TabFree users who
can view the dashboard, since the email contains the value.
"""

from __future__ import annotations

import datetime as dt
import math
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import and_, select, update

from tabfree_api.db import schema as t
from tabfree_api.db.store import now
from tabfree_api.jobs.common import can_view, expand, session_info
from tabfree_api.jobs.mail import Message
from tabfree_api.jobs.queue import Job, _aware
from tabfree_api.jobs.worker import Permanent, Retryable

OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "=": lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12),
    "!=": lambda a, b: not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12),
}
AGGREGATES = ("any", "all", "sum", "min", "max")


def check_condition(condition: Mapping[str, Any]) -> dict[str, Any]:
    operator = condition.get("operator")
    if operator not in OPERATORS:
        raise ValueError(f"operator must be one of {', '.join(OPERATORS)}")
    try:
        threshold = float(condition["threshold"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("the threshold must be a number") from None
    aggregate = condition.get("aggregate", "any")
    if aggregate not in AGGREGATES:
        raise ValueError(f"aggregate must be one of {', '.join(AGGREGATES)}")
    return {"operator": operator, "threshold": threshold, "aggregate": aggregate}


def evaluate(values: Sequence[Any], condition: Mapping[str, Any]) -> tuple[bool, float | None]:
    """(is the condition met, the value it was judged on)."""
    numbers = [
        float(v)
        for v in values
        if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v))
    ]
    if not numbers:
        return False, None
    test = OPERATORS[condition["operator"]]
    threshold = float(condition["threshold"])
    aggregate = condition.get("aggregate", "any")
    if aggregate in ("sum", "min", "max"):
        value = (
            float(sum(numbers))
            if aggregate == "sum"
            else (min(numbers) if aggregate == "min" else max(numbers))
        )
        return test(value, threshold), value
    hits = [n for n in numbers if test(n, threshold)]
    if aggregate == "all":
        return len(hits) == len(numbers), (
            min(numbers) if condition["operator"] in (">", ">=") else max(numbers)
        )
    return bool(hits), (hits[0] if hits else numbers[0])


def should_notify(
    repeat: str, was_firing: bool, firing: bool, last: dt.datetime | None, when: dt.datetime
) -> bool:
    if not firing:
        return False
    if repeat == "every_check":
        return True
    if repeat == "daily":
        return last is None or when - last >= dt.timedelta(hours=24)
    return not was_firing


def handle(ctx: Any, job: Job) -> Mapping[str, Any]:
    from tabfree_api.authoring.service import AuthoringService
    from tabfree_api.engines import EngineError

    site = ctx.site
    a = t.data_alerts.c
    key = and_(a.site_id == site.id, a.id == str(job.payload.get("alert")))
    with site.engine.connect() as conn:
        alert = conn.execute(select(t.data_alerts).where(key)).mappings().first()
    if alert is None:
        raise Permanent(f"no alert {job.payload.get('alert')!r}")
    if not alert["enabled"]:
        return {"skipped": "disabled"}
    dashboard = str(alert["dashboard_id"])
    if dashboard not in site.dashboards:
        raise Permanent(f"dashboard {dashboard!r} no longer exists")
    owner = session_info(site, str(alert["owner"]))
    if not can_view(site, owner, dashboard):
        raise Permanent(f"the owner {alert['owner']!r} can no longer view {dashboard!r}")
    manifest = site.bundle(dashboard).manifest
    try:
        result = ctx.service.query(
            manifest,
            str(alert["query"]),
            dict(alert["state"] or {}),
            {},
            AuthoringService(ctx).session(owner),
        )
    except EngineError as exc:
        raise Retryable(str(exc)) from None
    columns = [c["name"] for c in result["columns"]]
    if alert["measure"] not in columns:
        raise Permanent(f"query {alert['query']!r} has no column {alert['measure']!r}")
    index = columns.index(alert["measure"])
    condition = dict(alert["condition"])
    firing, value = evaluate([row[index] for row in result["rows"]], condition)
    when = now()
    notify = should_notify(
        str(alert["repeat"]), bool(alert["firing"]), firing, _aware(alert["last_notified_at"]), when
    )
    notified = 0
    if notify:
        notified = _notify(ctx, alert, value, condition, dashboard, str(manifest.get("title", dashboard)))
    values: dict[str, Any] = {"firing": firing, "last_value": {"value": value}, "last_checked_at": when}
    if notify:
        values["last_notified_at"] = when
    with site.engine.begin() as conn:
        conn.execute(update(t.data_alerts).where(key).values(**values))
    return {"firing": firing, "value": value, "notified": notified}


def _notify(
    ctx: Any,
    alert: Mapping[str, Any],
    value: float | None,
    condition: Mapping[str, Any],
    dashboard: str,
    title: str,
) -> int:
    site = ctx.site
    recipients, _problems = expand(site, alert["recipients"] or [{"user": alert["owner"]}])
    link = f"{ctx.settings.base_url.rstrip('/')}/dashboards/{urllib.parse.quote(dashboard)}"
    sent = 0
    for recipient in recipients:
        if recipient.username is None:
            continue  # alerts carry data: TabFree users with View only
        if not can_view(site, session_info(site, recipient.username), dashboard):
            continue
        text = (
            f"{alert['title']}\n\n{alert['measure']} is {value:g} "
            f"({condition.get('aggregate', 'any')} {condition['operator']} {condition['threshold']:g}).\n\n"
            f"{title}\n{link}\n"
        )
        ctx.mailer.send(Message(to=[recipient.address], subject=f"Alert: {alert['title']}", text=text))
        sent += 1
    ctx.audit(
        "alert_notify", str(alert["owner"]), resource_kind="dashboard", resource_id=dashboard, recipients=sent
    )
    return sent
