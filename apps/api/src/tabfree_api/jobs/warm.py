"""Cache warming (BUILD_PLAN P6.10): after a refresh, run the busiest dashboards' queries ahead of people.

Results are cached per compiled SQL, row-level-security context and data version (P2.7), so warming
runs each query **as the people who actually open the dashboard** (its most frequent recent viewers,
from the audit log), each with their own permissions; nobody's cache entry is filled with another
person's data. Only the default state is warmed (the first screen people see).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select

from tabfree_api.db import schema as t
from tabfree_api.db.store import now
from tabfree_api.jobs.common import NoSuchUser, can_view, session_info
from tabfree_api.jobs.queue import Job, JobQueue

TOP_DASHBOARDS = 10
VIEWERS_PER_DASHBOARD = 5
WINDOW_DAYS = 7


def busiest(ctx: Any, dashboards: list[str], *, limit: int) -> list[tuple[str, list[str]]]:
    """(dashboard, top viewers) for the most viewed of ``dashboards`` in the last week."""
    a = t.audit_events.c
    since = now() - dt.timedelta(days=WINDOW_DAYS)
    with ctx.site.engine.connect() as conn:
        rows = conn.execute(
            select(a.resource_id, a.actor, func.count())
            .where(
                a.site_id == ctx.site.id,
                a.action == "view",
                a.outcome == "ok",
                a.resource_kind == "dashboard",
                a.resource_id.in_(dashboards),
                a.at >= since,
            )
            .group_by(a.resource_id, a.actor)
        ).all()
    totals: dict[str, int] = {}
    viewers: dict[str, list[tuple[int, str]]] = {}
    for dashboard, actor, count in rows:
        totals[dashboard] = totals.get(dashboard, 0) + int(count)
        if actor:
            viewers.setdefault(dashboard, []).append((int(count), str(actor)))
    ranked = sorted(totals, key=lambda d: -totals[d])[:limit]
    return [
        (d, [u for _, u in sorted(viewers.get(d, []), reverse=True)[:VIEWERS_PER_DASHBOARD]]) for d in ranked
    ]


def enqueue_warm(ctx: Any, queue: JobQueue, model_id: str) -> int:
    using = [
        d for d in ctx.site.dashboards
        if model_id in {str(m) for m in (ctx.site.bundle(d).manifest.get("models") or {}).values()}
    ]  # fmt: skip
    count = 0
    for dashboard, viewers in busiest(ctx, using, limit=TOP_DASHBOARDS):
        if viewers and queue.enqueue(
            ctx.site.id, "warm", {"dashboard": dashboard, "viewers": viewers}, priority=-1,
            created_by=f"event:refresh:{model_id}", dedupe_key=f"warm:{dashboard}", max_attempts=1,
        ):  # fmt: skip
            count += 1
    return count


def handle(ctx: Any, job: Job) -> Mapping[str, Any]:
    from tabfree_api.authoring.service import AuthoringService

    dashboard = str(job.payload.get("dashboard"))
    if dashboard not in ctx.site.dashboards:
        return {"skipped": "no such dashboard"}
    manifest = ctx.site.bundle(dashboard).manifest
    service = AuthoringService(ctx)
    warmed = failed = 0
    for username in job.payload.get("viewers") or []:
        try:
            info = session_info(ctx.site, str(username))
        except NoSuchUser:
            continue
        if not can_view(ctx.site, info, dashboard):
            continue
        for name in manifest.get("queries", {}):
            try:
                ctx.service.query(manifest, name, {}, {}, service.session(info))
                warmed += 1
            except Exception:  # warming is best effort
                failed += 1
    return {"dashboard": dashboard, "queries": warmed, "failed": failed}
