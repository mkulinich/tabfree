"""Read-only Tableau Server inventory crawler (BUILD_PLAN P1.1).

Objects are collected in dependency order (identity → containers → data → presentation →
operations; the Migration SDK's order) into a SQLite manifest keyed by
``(server, site, type, luid)``. Re-runs are idempotent: unchanged objects (same ``updated_at`` and
content hash) are left alone, changed ones are updated, and every run is recorded. A failure in
one object type is recorded and the crawl continues with the next type.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

#: Crawl order; later types may reference earlier ones (owners, projects, data sources).
OBJECT_TYPES = (
    "users",
    "groups",
    "projects",
    "datasources",
    "workbooks",
    "views",
    "schedules",
    "tasks",
    "subscriptions",
)


@dataclass(frozen=True)
class Item:
    """One Tableau object as plain JSON-able data."""

    type: str
    luid: str
    name: str
    updated_at: str | None
    data: dict[str, Any]

    def content_hash(self) -> str:
        payload = json.dumps(self.data, sort_keys=True, default=str).encode()
        return hashlib.sha256(payload).hexdigest()


class InventorySource(Protocol):
    """Yields the objects of one type for one site. Implemented with TSC; faked in tests."""

    server: str
    site: str

    def items(self, object_type: str) -> Iterable[Item]: ...


@dataclass
class CrawlStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
  server TEXT NOT NULL, site TEXT NOT NULL, type TEXT NOT NULL, luid TEXT NOT NULL,
  name TEXT NOT NULL, updated_at TEXT, content_hash TEXT NOT NULL, data TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_run INTEGER NOT NULL,
  PRIMARY KEY (server, site, type, luid)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, site TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, stats TEXT
);
"""


class InventoryStore:
    """SQLite manifest of everything found on Tableau Server."""

    def __init__(self, path: Path | str) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def start_run(self, server: str, site: str) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (server, site, started_at) VALUES (?, ?, ?)", (server, site, _now())
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, stats: CrawlStats) -> None:
        payload = json.dumps(stats.__dict__)
        self.db.execute("UPDATE runs SET finished_at = ?, stats = ? WHERE id = ?", (_now(), payload, run_id))
        self.db.commit()

    def upsert(self, server: str, site: str, item: Item, run_id: int) -> str:
        """Store ``item``; return ``added``, ``updated`` or ``unchanged``."""
        key = (server, site, item.type, item.luid)
        digest = item.content_hash()
        row = self.db.execute(
            "SELECT updated_at, content_hash FROM objects "
            "WHERE server = ? AND site = ? AND type = ? AND luid = ?",
            key,
        ).fetchone()
        now = _now()
        if row is None:
            self.db.execute(
                "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*key, item.name, item.updated_at, digest, _dumps(item.data), now, now, run_id),
            )
            return "added"
        changed = row[0] != item.updated_at or row[1] != digest
        if changed:
            self.db.execute(
                "UPDATE objects SET name = ?, updated_at = ?, content_hash = ?, data = ?, "
                "last_seen = ?, last_run = ? WHERE server = ? AND site = ? AND type = ? AND luid = ?",
                (item.name, item.updated_at, digest, _dumps(item.data), now, run_id, *key),
            )
            return "updated"
        self.db.execute(
            "UPDATE objects SET last_seen = ?, last_run = ? "
            "WHERE server = ? AND site = ? AND type = ? AND luid = ?",
            (now, run_id, *key),
        )
        return "unchanged"

    def objects(self, object_type: str | None = None) -> Iterator[Item]:
        sql = "SELECT type, luid, name, updated_at, data FROM objects"
        params: tuple[str, ...] = ()
        if object_type:
            sql += " WHERE type = ?"
            params = (object_type,)
        for row in self.db.execute(sql + " ORDER BY type, name", params):
            yield Item(row[0], row[1], row[2], row[3], json.loads(row[4]))

    def missing_since(self, run_id: int) -> list[tuple[str, str, str]]:
        """Objects not seen in ``run_id`` (deleted on the server or no longer visible)."""
        rows = self.db.execute(
            "SELECT type, luid, name FROM objects WHERE last_run < ? ORDER BY type, name", (run_id,)
        )
        return [(r[0], r[1], r[2]) for r in rows]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, default=str)


def crawl(source: InventorySource, store: InventoryStore, types: Iterable[str] = OBJECT_TYPES) -> CrawlStats:
    """Collect every object type into ``store``; errors are recorded per type, not raised."""
    stats = CrawlStats()
    run_id = store.start_run(source.server, source.site)
    for object_type in types:
        count = 0
        try:
            for item in source.items(object_type):
                outcome = store.upsert(source.server, source.site, item, run_id)
                setattr(stats, outcome, getattr(stats, outcome) + 1)
                count += 1
        except Exception as exc:
            stats.errors[object_type] = f"{type(exc).__name__}: {exc}"
        stats.counts[object_type] = count
        store.db.commit()
    store.finish_run(run_id, stats)
    return stats
