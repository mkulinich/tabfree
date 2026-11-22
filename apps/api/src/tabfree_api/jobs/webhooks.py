"""Outgoing webhooks (BUILD_PLAN P6.9): events posted to subscribers' URLs, signed, retried.

Events: ``content.published``, ``content.deleted``, ``content.restored``, ``model.refreshed``,
``job.failed``, ``subscription.sent``, ``alert.fired``, ``comment.created``, or ``*`` for all. Each
delivery is a job (so it retries with backoff) and carries ``X-TabFree-Event``, a delivery id and
``X-TabFree-Signature: sha256=<HMAC of the body with the webhook's secret>``. Payloads hold ids and
names, never data values. URLs must be https (http only to localhost outside production).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select

from tabfree_api.db import schema as t
from tabfree_api.jobs.queue import Job, JobQueue
from tabfree_api.jobs.worker import Permanent, Retryable

EVENTS = (
    "content.published",
    "content.deleted",
    "content.restored",
    "model.refreshed",
    "job.failed",
    "subscription.sent",
    "alert.fired",
    "comment.created",
)

Post = Callable[[str, bytes, Mapping[str, str]], int]


def check_url(url: str, environment: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if (
        parts.scheme == "http"
        and parts.hostname in ("localhost", "127.0.0.1")
        and environment != "production"
    ):
        return url
    raise ValueError("webhook URLs must be https")


def _post(url: str, body: bytes, headers: Mapping[str, str]) -> int:
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))  # noqa: S310 - checked
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


#: Replaced in tests.
post: Post = _post


def emit(ctx: Any, event: str, data: Mapping[str, Any]) -> int:
    """Queue a delivery to every enabled webhook that listens for ``event``."""
    site = getattr(ctx, "site", None)
    if site is None or not hasattr(site, "engine"):
        return 0
    w = t.webhooks.c
    with site.engine.connect() as conn:
        hooks = conn.execute(select(w.id, w.events).where(w.site_id == site.id, w.enabled)).all()
    queue = JobQueue(site.engine)
    count = 0
    for hook_id, events in hooks:
        if event in (events or []) or "*" in (events or []):
            queue.enqueue(
                site.id, "webhook",
                {"webhook": hook_id, "event": event, "data": dict(data), "delivery": str(uuid.uuid4())},
                max_attempts=5, created_by=f"event:{event}",
            )  # fmt: skip
            count += 1
    return count


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(ctx: Any, job: Job) -> Mapping[str, Any]:
    w = t.webhooks.c
    with ctx.site.engine.connect() as conn:
        hook = (
            conn.execute(
                select(t.webhooks).where(w.site_id == ctx.site.id, w.id == str(job.payload.get("webhook")))
            )
            .mappings()
            .first()
        )
    if hook is None or not hook["enabled"]:
        return {"skipped": "webhook removed or disabled"}
    try:
        url = check_url(str(hook["url"]), ctx.settings.environment)
    except ValueError as exc:
        raise Permanent(str(exc)) from None
    body = json.dumps(
        {
            "event": job.payload["event"],
            "site": ctx.site.id,
            "delivery": job.payload["delivery"],
            "data": job.payload.get("data", {}),
        },
        separators=(",", ":"),
    ).encode()  # fmt: skip
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TabFree-Webhooks/1",
        "X-TabFree-Event": str(job.payload["event"]),
        "X-TabFree-Delivery": str(job.payload["delivery"]),
        "X-TabFree-Signature": signature(str(hook["secret"]), body),
    }
    try:
        status = post(url, body, headers)
    except OSError as exc:
        raise Retryable(f"delivery failed: {exc}") from None
    if status == 429 or status >= 500:
        raise Retryable(f"the receiver answered HTTP {status}")
    if status >= 400:
        raise Permanent(f"the receiver answered HTTP {status}")
    return {"status": status}
