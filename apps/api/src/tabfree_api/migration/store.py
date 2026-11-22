"""Migration items, their history and the records kept for later phases (database tables in schema)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Engine, and_, delete, func, insert, select, update

from tabfree_api.db import schema as t
from tabfree_api.db.store import now

#: Reviewer actions and the status each one leads to (P5.6).
ACTIONS = {
    "approve": "approved",
    "request_changes": "changes_requested",
    "rebuild": "rebuild",
    "retire": "retired",
    "reopen": "review",
}
#: Statuses an action may start from.
FROM = {
    "approve": {"converted", "review", "changes_requested"},
    "request_changes": {"converted", "review", "changes_requested"},
    "rebuild": {"converted", "manual", "review", "changes_requested"},
    "retire": {"converted", "manual", "review", "changes_requested", "rebuild"},
    "reopen": {"rebuild", "retired", "changes_requested"},
}
_LIST_COLUMNS = (
    "id",
    "title",
    "workbook",
    "project_id",
    "owner",
    "wave",
    "usage",
    "status",
    "draft_id",
    "fidelity",
    "repair_iterations",
    "reviewer",
    "note",
    "published_revision",
    "updated_at",
)


class MigrationError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class MigrationStore:
    def __init__(self, engine: Engine, site_id: str) -> None:
        self.engine = engine
        self.site_id = site_id

    def _key(self, item_id: str) -> Any:
        c = t.migration_items.c
        return and_(c.site_id == self.site_id, c.id == item_id)

    # -- items ----------------------------------------------------------------------------------------
    def upsert(self, item_id: str, values: Mapping[str, Any]) -> None:
        when = now()
        with self.engine.begin() as conn:
            exists = conn.execute(select(t.migration_items.c.id).where(self._key(item_id))).scalar()
            if exists is None:
                conn.execute(
                    insert(t.migration_items).values(
                        site_id=self.site_id, id=item_id, created_at=when, updated_at=when, **values
                    )
                )
            else:
                conn.execute(
                    update(t.migration_items).where(self._key(item_id)).values(updated_at=when, **values)
                )

    def get(self, item_id: str, *, images: bool = False) -> dict[str, Any] | None:
        cols = [c for c in t.migration_items.c if images or c.name not in ("reference_png", "render_png")]
        with self.engine.connect() as conn:
            row = conn.execute(select(*cols).where(self._key(item_id))).mappings().first()
        if row is None:
            return None
        out = dict(row)
        out.pop("site_id", None)
        out["has_reference_png"] = self._has(item_id, "reference_png")
        out["has_render_png"] = self._has(item_id, "render_png")
        return out

    def _has(self, item_id: str, column: str) -> bool:
        col = getattr(t.migration_items.c, column)
        with self.engine.connect() as conn:
            return bool(conn.execute(select(col.is_not(None)).where(self._key(item_id))).scalar())

    def image(self, item_id: str, column: str) -> bytes | None:
        if column not in ("reference_png", "render_png"):
            raise ValueError(column)
        col = getattr(t.migration_items.c, column)
        with self.engine.connect() as conn:
            value = conn.execute(select(col).where(self._key(item_id))).scalar()
        return bytes(value) if value is not None else None

    def list_items(
        self, *, wave: int | None = None, status: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        c = t.migration_items.c
        query = select(*(getattr(c, name) for name in _LIST_COLUMNS)).where(c.site_id == self.site_id)
        if wave is not None:
            query = query.where(c.wave == wave)
        if status is not None:
            query = query.where(c.status == status)
        query = query.order_by(c.wave.is_(None), c.wave, c.usage.desc(), c.title).limit(limit)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(query).mappings().all()]

    def summary(self) -> dict[str, Any]:
        c = t.migration_items.c
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(c.wave, c.status, func.count())
                .where(c.site_id == self.site_id)
                .group_by(c.wave, c.status)
            ).all()
        waves: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        for wave, status, count in rows:
            key = "retired" if wave is None else str(wave)
            waves.setdefault(key, {})[status] = int(count)
            totals[status] = totals.get(status, 0) + int(count)
        return {"waves": waves, "totals": totals, "items": sum(totals.values())}

    def act(self, item_id: str, action: str, *, by: str, note: str = "") -> dict[str, Any]:
        """A reviewer action; checks the item may take it. Publishing for approve is the caller's."""
        if action not in ACTIONS:
            raise MigrationError(422, f"unknown action {action!r}")
        item = self.get(item_id)
        if item is None:
            raise MigrationError(404, f"no migration item {item_id!r}")
        if item["status"] not in FROM[action]:
            raise MigrationError(
                409, f"{item_id!r} is {item['status']}; {action.replace('_', ' ')} is not possible"
            )
        self.upsert(item_id, {"status": ACTIONS[action], "reviewer": by, "note": note[:4000]})
        self.event(item_id, action, by=by, note=note)
        return self.get(item_id) or {}

    # -- history --------------------------------------------------------------------------------------
    def event(
        self, item_id: str, action: str, *, by: str | None = None, note: str = "", data: Any = None
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(t.migration_events).values(
                    site_id=self.site_id,
                    item_id=item_id,
                    action=action,
                    by=by,
                    note=note[:4000],
                    data=data,
                    created_at=now(),
                )
            )

    def events(self, item_id: str, limit: int = 200) -> list[dict[str, Any]]:
        e = t.migration_events.c
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(e.action, e.by, e.note, e.data, e.created_at)
                .where(e.site_id == self.site_id, e.item_id == item_id)
                .order_by(e.id.desc())
                .limit(limit)
            ).mappings()
            return [dict(r) for r in rows]

    # -- records --------------------------------------------------------------------------------------
    def put_record(self, kind: str, key: str, data: Any, status: str = "pending") -> None:
        r = t.migration_records.c
        where = and_(r.site_id == self.site_id, r.kind == kind, r.key == key[:200])
        with self.engine.begin() as conn:
            conn.execute(delete(t.migration_records).where(where))
            conn.execute(
                insert(t.migration_records).values(
                    site_id=self.site_id, kind=kind, key=key[:200], data=data, status=status, updated_at=now()
                )
            )

    def records(self, kind: str, keys: Sequence[str] | None = None) -> list[dict[str, Any]]:
        r = t.migration_records.c
        query = select(r.key, r.data, r.status, r.updated_at).where(r.site_id == self.site_id, r.kind == kind)
        if keys is not None:
            query = query.where(r.key.in_(list(keys)))
        with self.engine.connect() as conn:
            return [dict(x) for x in conn.execute(query.order_by(r.key)).mappings().all()]
