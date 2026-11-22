"""Telling people when their scheduled work fails (P6.1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tabfree_api.db import schema as t
from tabfree_api.jobs.mail import Message
from tabfree_api.jobs.queue import Job

_OWNERS = {
    "subscription": (t.subscriptions, "subscription", "owner"),
    "alert": (t.data_alerts, "alert", "owner"),
    "refresh": (t.refresh_tasks, "task", "owner"),
}


def owner_of(ctx: Any, job: Job) -> str | None:
    if job.kind in _OWNERS:
        table, key, column = _OWNERS[job.kind]
        with ctx.site.engine.connect() as conn:
            row = conn.execute(
                select(table.c[column]).where(
                    table.c.site_id == ctx.site.id, table.c.id == str(job.payload.get(key))
                )
            ).first()
        if row is not None and row[0]:
            return str(row[0])
    if job.created_by and not job.created_by.startswith(("schedule", "event:")):
        return job.created_by
    return None


def job_failed(ctx: Any, job: Job, error: str) -> bool:
    owner = owner_of(ctx, job)
    user = ctx.site.users.get(owner) if owner else None
    if user is None or not user.email:
        return False
    link = f"{ctx.settings.base_url.rstrip('/')}/jobs"
    ctx.mailer.send(
        Message(
            to=[user.email],
            subject=f"TabFree: a {job.kind} job failed",
            text=(
                f"Job {job.id} ({job.kind}) failed after {job.attempts} attempt(s):\n\n{error[:1000]}\n\n"
                f"Job history: {link}\n"
            ),
        )
    )
    return True
