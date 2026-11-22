"""Subscriptions (BUILD_PLAN P6.3): a dashboard by email, rendered for each recipient.

Each TabFree recipient gets the dashboard rendered **as themselves** (their permissions and row-level
security), so nobody receives data they could not see in the portal; a recipient without View on the
dashboard is skipped and reported. External addresses (outside ``mail_internal_domains``) only receive
once the owner has confirmed them, and then render with the owner's access, which is why the owner
must confirm. "Only if data" skips a recipient whose queries all come back empty. Every email carries
an unsubscribe link (and a List-Unsubscribe header), and unsubscribed addresses are never sent to.
An optional Slack or Microsoft Teams webhook gets the subject and a link, never the data.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, select, update

from tabfree_api.db import schema as t
from tabfree_api.db.store import now
from tabfree_api.jobs.common import Recipient, can_view, expand, is_internal, session_info, sign
from tabfree_api.jobs.mail import Attachment, Message
from tabfree_api.jobs.queue import Job
from tabfree_api.jobs.worker import Permanent, Retryable

CONTENT_TYPES = {"png": "image/png", "pdf": "application/pdf"}


def suppressed(site: Any, subscription_id: str) -> set[str]:
    m = t.mail_suppressions.c
    with site.engine.connect() as conn:
        rows = conn.execute(
            select(m.address).where(m.site_id == site.id, m.subscription_id.in_((subscription_id, "*")))
        )
        return {str(a).lower() for (a,) in rows}


def has_data(ctx: Any, info: Any, dashboard: str, state: Mapping[str, Any]) -> bool:
    from tabfree_api.authoring.service import AuthoringService

    manifest = ctx.site.bundle(dashboard).manifest
    session = AuthoringService(ctx).session(info)
    for name in manifest.get("queries", {}):
        result = ctx.service.query(manifest, name, state, {}, session)
        if result.get("rows"):
            return True
    return False


def unsubscribe_url(ctx: Any, subscription_id: str, address: str) -> str:
    token = sign(ctx.settings, "unsubscribe", ctx.site.id, subscription_id, address.lower())
    return f"{ctx.settings.base_url.rstrip('/')}/unsubscribe?token={urllib.parse.quote(token)}"


def _state(ctx: Any, sub: Mapping[str, Any]) -> dict[str, Any]:
    state = dict(sub["state"] or {})
    if sub["custom_view_id"]:
        c = t.custom_views.c
        with ctx.site.engine.connect() as conn:
            view = conn.execute(
                select(c.state).where(c.site_id == ctx.site.id, c.id == sub["custom_view_id"])
            ).first()
        if view is not None:
            state = dict(view[0] or {})
    return state


def post_webhook(url: str, subject: str, link: str) -> None:
    """Slack and Teams incoming webhooks: a line of text with the link (no data, no images)."""
    if not url.startswith("https://"):
        raise Permanent("subscription webhooks must be https")
    body = json.dumps({"text": f"{subject}: {link}"}).encode()
    request = urllib.request.Request(  # noqa: S310 - https checked above
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - https checked
            response.read(1024)
    except OSError as exc:
        raise Retryable(f"webhook: {exc}") from None


def handle(ctx: Any, job: Job) -> Mapping[str, Any]:
    site = ctx.site
    s = t.subscriptions.c
    key = and_(s.site_id == site.id, s.id == str(job.payload.get("subscription")))
    with site.engine.connect() as conn:
        sub = conn.execute(select(t.subscriptions).where(key)).mappings().first()
    if sub is None:
        raise Permanent(f"no subscription {job.payload.get('subscription')!r}")
    if sub["status"] != "active":
        return {"skipped": f"subscription is {sub['status']}"}
    dashboard = str(sub["dashboard_id"])
    if dashboard not in site.dashboards:
        raise Permanent(f"dashboard {dashboard!r} no longer exists")
    renderer = getattr(ctx, "renderer", None)
    if renderer is None:
        raise Permanent("no renderer: install tabfree-api[render] and Chromium for subscriptions")
    mailer = ctx.mailer
    owner = session_info(site, str(sub["owner"]))
    if not can_view(site, owner, dashboard):
        raise Permanent(f"the owner {sub['owner']!r} can no longer view {dashboard!r}")
    state = _state(ctx, sub)
    fmt = str(sub["format"]) if sub["format"] in CONTENT_TYPES else "png"
    recipients, problems = expand(site, sub["recipients"] or [])
    stop = suppressed(site, str(sub["id"]))
    title = str(site.bundle(dashboard).manifest.get("title", dashboard))
    link = f"{ctx.settings.base_url.rstrip('/')}/dashboards/{urllib.parse.quote(dashboard)}"
    already = set(job.payload.get("sent") or [])
    sent: list[str] = []
    skipped: list[dict[str, str]] = [{"reason": p} for p in problems]
    for recipient in recipients:
        address = recipient.address.lower()
        if address in stop or address in already:
            continue
        viewer = _viewer(ctx, sub, recipient, owner, skipped)
        if viewer is None:
            continue
        if sub["only_if_data"] and not has_data(ctx, viewer, dashboard, state):
            skipped.append({"address": address, "reason": "no data"})
            continue
        try:
            image = renderer.render(viewer, dashboard, state, fmt)
        except Exception as exc:
            raise Retryable(f"render failed: {exc}") from None
        unsubscribe = unsubscribe_url(ctx, str(sub["id"]), address)
        text = (
            f"{sub['message']}\n\n" if sub["message"] else ""
        ) + f"{title}\n{link}\n\nUnsubscribe: {unsubscribe}\n"
        mailer.send(
            Message(
                to=[recipient.address],
                subject=str(sub["subject"]),
                text=text,
                attachments=[Attachment(f"{dashboard}.{fmt}", CONTENT_TYPES[fmt], image)],
                headers={
                    "List-Unsubscribe": f"<{unsubscribe}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
            )
        )
        sent.append(address)
    if sub["webhook_url"] and not (sub["only_if_data"] and not sent):
        post_webhook(str(sub["webhook_url"]), str(sub["subject"]), link)
    with site.engine.begin() as conn:
        conn.execute(update(t.subscriptions).where(key).values(last_sent_at=now()))
    ctx.audit(
        "subscription_send",
        str(sub["owner"]),
        resource_kind="dashboard",
        resource_id=dashboard,
        sent=len(sent),
        skipped=len(skipped),
    )
    return {"sent": len(sent), "skipped": skipped, "at": dt.datetime.now(dt.UTC).isoformat()}


def _viewer(
    ctx: Any, sub: Mapping[str, Any], recipient: Recipient, owner: Any, skipped: list[dict[str, str]]
) -> Any:
    """Who the dashboard is rendered as for this recipient, or None to skip them."""
    site = ctx.site
    if recipient.username is not None:
        info = session_info(site, recipient.username)
        if not can_view(site, info, str(sub["dashboard_id"])):
            skipped.append({"address": recipient.address.lower(), "reason": "no View permission"})
            return None
        return info
    if not is_internal(ctx.settings, recipient.address):
        confirmed = {
            str(e.get("email", "")).lower() for e in sub["recipients"] or [] if e.get("confirmed") is True
        }
        if recipient.address.lower() not in confirmed:
            skipped.append({"address": recipient.address.lower(), "reason": "external address not confirmed"})
            return None
    return owner
