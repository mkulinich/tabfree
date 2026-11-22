"""Schedules and the scheduler loop (P6.1, ADR-013).

A due schedule enqueues one job per task attached to it (refresh tasks, active subscriptions, enabled
alerts) and moves its ``next_run_at`` forward with a conditional update, so two schedulers never fire
the same run. Events do the same for tasks that run "after a refresh" (:func:`after_refresh`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, and_, delete, insert, select, update

from tabfree_api.db import schema as t
from tabfree_api.jobs.queue import Clock, JobQueue, _aware
from tabfree_api.jobs.schedule import ScheduleError, describe, next_run, validate


class SchedulesStore:
    def __init__(self, engine: Engine, site_id: str, *, clock: Clock) -> None:
        self.engine = engine
        self.site_id = site_id
        self.clock = clock

    def _key(self, schedule_id: str) -> Any:
        s = t.schedules.c
        return and_(s.site_id == self.site_id, s.id == schedule_id)

    def create(self, name: str, spec: Mapping[str, Any], *, enabled: bool = True) -> dict[str, Any]:
        spec = validate(spec)
        when = self.clock()
        sid = str(uuid.uuid4())
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(t.schedules.c.id).where(
                    t.schedules.c.site_id == self.site_id, t.schedules.c.name == name
                )
            ).first()
            if exists is not None:
                raise ScheduleError(f"a schedule named {name!r} exists")
            conn.execute(
                insert(t.schedules).values(
                    site_id=self.site_id,
                    id=sid,
                    name=name[:200],
                    spec=spec,
                    enabled=enabled,
                    next_run_at=next_run(spec, when) if enabled else None,
                    created_at=when,
                )
            )
        return self.get(sid) or {}

    def update(
        self,
        schedule_id: str,
        *,
        name: str | None = None,
        spec: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self.get(schedule_id)
        if current is None:
            raise ScheduleError(f"no schedule {schedule_id!r}")
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name[:200]
        new_spec = validate(spec) if spec is not None else current["spec"]
        on = current["enabled"] if enabled is None else enabled
        values.update(spec=new_spec, enabled=on, next_run_at=next_run(new_spec, self.clock()) if on else None)
        with self.engine.begin() as conn:
            conn.execute(update(t.schedules).where(self._key(schedule_id)).values(**values))
        return self.get(schedule_id) or {}

    def delete(self, schedule_id: str) -> bool:
        with self.engine.begin() as conn:
            for table in (t.refresh_tasks, t.subscriptions, t.data_alerts):
                used = conn.execute(
                    select(table.c.id).where(
                        table.c.site_id == self.site_id, table.c.schedule_id == schedule_id
                    )
                ).first()
                if used is not None:
                    raise ScheduleError("the schedule is in use; move its tasks to another schedule first")
            return conn.execute(delete(t.schedules).where(self._key(schedule_id))).rowcount == 1

    def get(self, schedule_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(t.schedules).where(self._key(schedule_id))).mappings().first()
        return self._dict(dict(row)) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t.schedules).where(t.schedules.c.site_id == self.site_id).order_by(t.schedules.c.name)
            ).mappings()
            return [self._dict(dict(r)) for r in rows]

    @staticmethod
    def _dict(row: Mapping[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in row.items() if k != "site_id"}
        for key in ("next_run_at", "last_run_at", "created_at"):
            value = _aware(out.get(key))
            out[key] = value.isoformat() if value else None
        try:
            out["description"] = describe(out["spec"])
        except ScheduleError:
            out["description"] = "invalid schedule"
        return out


def _tasks(conn: Any, site_id: str, column: str, value: str) -> list[tuple[str, str, dict[str, Any]]]:
    """(job kind, dedupe key, payload) for every active task attached by ``column`` = ``value``."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    r, s, a = t.refresh_tasks.c, t.subscriptions.c, t.data_alerts.c
    if column == "schedule_id":
        for (rid,) in conn.execute(
            select(r.id).where(r.site_id == site_id, r.schedule_id == value, r.enabled)
        ):
            out.append(("refresh", f"refresh:{rid}", {"task": rid}))
    for (sid,) in conn.execute(
        select(s.id).where(s.site_id == site_id, getattr(s, column) == value, s.status == "active")
    ):
        out.append(("subscription", f"subscription:{sid}", {"subscription": sid}))
    for (aid,) in conn.execute(
        select(a.id).where(a.site_id == site_id, getattr(a, column) == value, a.enabled)
    ):
        out.append(("alert", f"alert:{aid}", {"alert": aid}))
    return out


class Scheduler:
    def __init__(self, engine: Engine, queue: JobQueue, *, clock: Clock) -> None:
        self.engine = engine
        self.queue = queue
        self.clock = clock

    def tick(self) -> int:
        """Fire every due schedule once; returns the number of jobs enqueued."""
        when = self.clock()
        s = t.schedules.c
        enqueued = 0
        with self.engine.connect() as conn:
            due = conn.execute(
                select(s.site_id, s.id, s.spec, s.next_run_at).where(s.enabled, s.next_run_at <= when)
            ).all()
        for site_id, schedule_id, spec, previous in due:
            try:
                following = next_run(spec, when)
            except ScheduleError:
                following = None
            with self.engine.begin() as conn:
                won = conn.execute(
                    update(t.schedules)
                    .where(s.site_id == site_id, s.id == schedule_id, s.next_run_at == previous)
                    .values(next_run_at=following, last_run_at=when, enabled=following is not None)
                ).rowcount
                if won != 1:
                    continue  # another scheduler fired it
                tasks = _tasks(conn, site_id, "schedule_id", schedule_id)
            for kind, key, payload in tasks:
                if self.queue.enqueue(
                    site_id, kind, payload, schedule_id=schedule_id, created_by="schedule", dedupe_key=key
                ):
                    enqueued += 1
        return enqueued


def after_refresh(engine: Engine, queue: JobQueue, site_id: str, model: str) -> int:
    """Enqueue the subscriptions and alerts that run after ``model`` refreshes; returns the count."""
    with engine.connect() as conn:
        tasks = [x for x in _tasks(conn, site_id, "after_refresh_of", model) if x[0] != "refresh"]
    count = 0
    for kind, key, payload in tasks:
        if queue.enqueue(site_id, kind, payload, created_by=f"event:refresh:{model}", dedupe_key=key):
            count += 1
    return count


def due_in(spec: Mapping[str, Any], when: dt.datetime) -> dt.datetime:
    return next_run(spec, when)
