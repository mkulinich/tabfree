"""The job queue in the metadata database (ADR-013): enqueue, claim with a lease, finish, retry."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, and_, delete, func, insert, or_, select, update

from tabfree_api.db import schema as t
from tabfree_api.db.store import now as db_now

Clock = Callable[[], dt.datetime]
#: Seconds before retry n (1-based): 30 s, 2 min, 8 min, then 30 min.
BACKOFF = (30, 120, 480, 1800)


@dataclass(frozen=True)
class Job:
    id: int
    site_id: str
    kind: str
    payload: Mapping[str, Any]
    attempts: int
    max_attempts: int
    schedule_id: str | None
    created_by: str | None


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def job_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("run_at", "created_at", "started_at", "finished_at", "locked_until"):
        value = _aware(out.get(key))
        out[key] = value.isoformat() if value else None
    return out


class JobQueue:
    def __init__(self, engine: Engine, *, clock: Clock = db_now) -> None:
        self.engine = engine
        self.clock = clock

    def enqueue(
        self,
        site_id: str,
        kind: str,
        payload: Mapping[str, Any],
        *,
        run_at: dt.datetime | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        schedule_id: str | None = None,
        created_by: str | None = None,
        dedupe_key: str | None = None,
    ) -> int | None:
        """A new queued job's id; None when a queued or running job has the same dedupe key."""
        j = t.jobs.c
        when = self.clock()
        with self.engine.begin() as conn:
            if dedupe_key is not None:
                existing = conn.execute(
                    select(j.id).where(
                        j.site_id == site_id,
                        j.dedupe_key == dedupe_key,
                        j.status.in_(("queued", "running")),
                    )
                ).first()
                if existing is not None:
                    return None
            result = conn.execute(
                insert(t.jobs).values(
                    site_id=site_id,
                    kind=kind,
                    payload=dict(payload),
                    status="queued",
                    priority=priority,
                    run_at=run_at or when,
                    attempts=0,
                    max_attempts=max_attempts,
                    schedule_id=schedule_id,
                    created_by=created_by,
                    dedupe_key=dedupe_key,
                    created_at=when,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key else None

    def claim(
        self,
        worker: str,
        *,
        site_id: str | None = None,
        kinds: Sequence[str] | None = None,
        lease_s: int = 300,
    ) -> Job | None:
        """The next due job, locked for ``lease_s`` seconds. Expired leases are claimed again."""
        j = t.jobs.c
        when = self.clock()
        due = or_(
            and_(j.status == "queued", j.run_at <= when),
            and_(j.status == "running", j.locked_until < when),
        )
        query = select(j.id).where(due)
        if site_id is not None:
            query = query.where(j.site_id == site_id)
        if kinds:
            query = query.where(j.kind.in_(list(kinds)))
        query = query.order_by(j.priority.desc(), j.run_at, j.id).limit(1).with_for_update(skip_locked=True)
        with self.engine.begin() as conn:
            row = conn.execute(query).first()
            if row is None:
                return None
            claimed = conn.execute(
                update(t.jobs)
                .where(j.id == row[0], due)
                .values(
                    status="running",
                    locked_by=worker[:100],
                    locked_until=when + dt.timedelta(seconds=lease_s),
                    attempts=j.attempts + 1,
                    started_at=when,
                )
            )
            if claimed.rowcount != 1:
                return None
            job = conn.execute(select(t.jobs).where(j.id == row[0])).mappings().one()
        return Job(
            int(job["id"]),
            str(job["site_id"]),
            str(job["kind"]),
            dict(job["payload"] or {}),
            int(job["attempts"]),
            int(job["max_attempts"]),
            job["schedule_id"],
            job["created_by"],
        )

    def complete(self, job_id: int, result: Mapping[str, Any] | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.jobs)
                .where(t.jobs.c.id == job_id)
                .values(
                    status="succeeded",
                    result=dict(result or {}),
                    error=None,
                    finished_at=self.clock(),
                    locked_by=None,
                    locked_until=None,
                )
            )

    def fail(self, job_id: int, error: str, *, retry: bool = True) -> str:
        """Record a failure; requeue with backoff while attempts remain. Returns the new status."""
        j = t.jobs.c
        when = self.clock()
        with self.engine.begin() as conn:
            row = conn.execute(select(j.attempts, j.max_attempts).where(j.id == job_id)).first()
            if row is None:
                return "missing"
            attempts, max_attempts = int(row[0]), int(row[1])
            if retry and attempts < max_attempts:
                delay = BACKOFF[min(attempts, len(BACKOFF)) - 1]
                values: dict[str, Any] = {"status": "queued", "run_at": when + dt.timedelta(seconds=delay)}
            else:
                values = {"status": "failed", "finished_at": when}
            conn.execute(
                update(t.jobs)
                .where(j.id == job_id)
                .values(error=error[:4000], locked_by=None, locked_until=None, **values)
            )
            return str(values["status"])

    def cancel(self, job_id: int) -> bool:
        j = t.jobs.c
        with self.engine.begin() as conn:
            done = conn.execute(
                update(t.jobs)
                .where(j.id == job_id, j.status == "queued")
                .values(status="cancelled", finished_at=self.clock())
            )
            return done.rowcount == 1

    def retry(self, job_id: int, *, by: str | None) -> int | None:
        """Run a finished job again, as a new job with the same kind and payload."""
        job = self.get(job_id)
        if job is None or job["status"] not in ("failed", "cancelled", "succeeded"):
            return None
        return self.enqueue(
            str(job["site_id"]),
            str(job["kind"]),
            job["payload"] or {},
            schedule_id=job["schedule_id"],
            created_by=by,
            max_attempts=int(job["max_attempts"]),
        )

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(t.jobs).where(t.jobs.c.id == job_id)).mappings().first()
        return job_dict(dict(row)) if row else None

    def list(
        self,
        site_id: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        created_by: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        j = t.jobs.c
        query = select(t.jobs).where(j.site_id == site_id)
        if status:
            query = query.where(j.status == status)
        if kind:
            query = query.where(j.kind == kind)
        if created_by:
            query = query.where(j.created_by == created_by)
        with self.engine.connect() as conn:
            rows = conn.execute(query.order_by(j.id.desc()).limit(min(limit, 1000))).mappings().all()
        return [job_dict(dict(r)) for r in rows]

    def counts(self, site_id: str) -> dict[str, int]:
        j = t.jobs.c
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(j.status, func.count()).where(j.site_id == site_id).group_by(j.status)
            ).all()
        return {str(s): int(n) for s, n in rows}

    def prune(self, older_than: dt.datetime) -> int:
        """Delete finished jobs older than ``older_than`` (job history retention)."""
        j = t.jobs.c
        with self.engine.begin() as conn:
            result = conn.execute(
                delete(t.jobs).where(
                    j.status.in_(("succeeded", "failed", "cancelled")), j.finished_at < older_than
                )
            )
            return int(result.rowcount or 0)
