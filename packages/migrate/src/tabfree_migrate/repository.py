"""Repository harvest (BUILD_PLAN P1.3): what REST does not expose, read from ``workgroup``.

Tableau Server keeps usage and some content details only in its PostgreSQL repository. With
``tsm data-access repository-access enable --repository-username readonly`` an administrator
opens the ``workgroup`` database (port 8060 by default) to the ``readonly`` user. This module reads:

- usage: ``views_stats``, ``http_requests``, ``historical_events`` (with their ``hist_*`` and
  ``historical_event_types`` lookups)
- subscription and data-driven alert details: ``subscriptions``, ``subscriptions_views``,
  ``subscriptions_workbooks``, ``data_alerts``, ``data_alerts_recipients``
- comments: ``comments``
- sizes: ``workbooks.size``, ``datasources.size`` and ``extracts``
- ``views`` (id → LUID), so usage can be joined to REST and Metadata API objects

Safety: every query is a parameterised ``SELECT``, checked by :func:`ensure_select` before it runs.
The psycopg connection is opened read-only (``default_transaction_read_only``) with a statement
timeout, and each query pages by ``id`` with a row ``LIMIT`` plus a per-run row cap. Request URIs
from ``http_requests`` lose their query string and trusted-ticket path segments before storage.

**Unverified:** table and column names come from Tableau's published *Workgroup data dictionary*
and community usage, not from a live server. They change between versions; verify them against
the target server (``\\d views_stats`` etc.) before relying on the output. A query that fails on
one table is recorded and the harvest carries on with the next.

The real connection uses psycopg 3 from the optional ``repository`` extra, imported lazily. Tests
run the same SQL on SQLite through :class:`SqliteRepository`.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

from tabfree_migrate.tableau import ConfigError

DEFAULT_PAGE_SIZE = 5_000
DEFAULT_MAX_ROWS = 2_000_000
DEFAULT_STATEMENT_TIMEOUT_MS = 120_000


class RepositoryReader(Protocol):
    """Runs one read-only, parameterised SELECT and returns rows as dicts."""

    server: str

    def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|vacuum|lock)\b",
    re.IGNORECASE,
)


def ensure_select(sql: str) -> str:
    """Reject anything but a single SELECT (or WITH … SELECT) statement."""
    text = sql.strip()
    if not re.match(r"(?is)^(select|with)\b", text):
        raise ValueError("repository queries must be SELECT statements")
    if ";" in text:
        raise ValueError("repository queries must be a single statement")
    if _FORBIDDEN.search(text):
        raise ValueError("repository query contains a write or DDL keyword")
    return text


_TRUSTED = re.compile(r"/trusted/[^/?#]+", re.IGNORECASE)


def redact_uri(uri: Any) -> Any:
    """Drop the query string (filter values, tokens) and trusted-ticket segments from a request URI."""
    if not isinstance(uri, str):
        return uri
    return _TRUSTED.sub("/trusted/REDACTED", uri.split("?", 1)[0].split("#", 1)[0])


def _redact_request(row: dict[str, Any]) -> dict[str, Any]:
    if "http_request_uri" in row:
        row["http_request_uri"] = redact_uri(row["http_request_uri"])
    return row


@dataclass(frozen=True)
class RepoQuery:
    """One source table. ``sql`` must keyset-page on ``id`` with ``%(after_id)s`` and ``%(limit)s``.

    ``incremental`` sources are append-only, so later runs continue after the highest id seen and
    may also bound history with ``%(since)s``. Other sources are re-read in full on every run.
    """

    name: str
    sql: str
    incremental: bool = False
    uses_since: bool = False
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None


QUERIES: tuple[RepoQuery, ...] = (
    RepoQuery(
        "views_stats",
        "SELECT vs.id, vs.view_id, vs.user_id, vs.site_id, vs.nviews, vs.time "
        "FROM views_stats vs WHERE vs.id > %(after_id)s ORDER BY vs.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "http_requests",
        "SELECT r.id, r.controller, r.action, r.http_request_uri, r.currentsheet, r.status, "
        "r.created_at, r.completed_at, r.user_id, r.site_id, r.vizql_session "
        "FROM http_requests r WHERE r.id > %(after_id)s AND r.created_at >= %(since)s "
        "ORDER BY r.id LIMIT %(limit)s",
        incremental=True,
        uses_since=True,
        transform=_redact_request,
    ),
    RepoQuery(
        "historical_events",
        "SELECT he.id, he.created_at, het.name AS event_type, het.action_type, he.is_failure, "
        "he.duration_in_ms, hu.user_id AS actor_user_id, hv.view_id, hw.workbook_id, "
        "hd.datasource_id, hs.site_id "
        "FROM historical_events he "
        "LEFT JOIN historical_event_types het ON het.type_id = he.historical_event_type_id "
        "LEFT JOIN hist_users hu ON hu.id = he.hist_actor_user_id "
        "LEFT JOIN hist_views hv ON hv.id = he.hist_view_id "
        "LEFT JOIN hist_workbooks hw ON hw.id = he.hist_workbook_id "
        "LEFT JOIN hist_datasources hd ON hd.id = he.hist_datasource_id "
        "LEFT JOIN hist_sites hs ON hs.id = he.hist_actor_site_id "
        "WHERE he.id > %(after_id)s AND he.created_at >= %(since)s ORDER BY he.id LIMIT %(limit)s",
        incremental=True,
        uses_since=True,
    ),
    RepoQuery(
        "subscriptions",
        "SELECT s.* FROM subscriptions s WHERE s.id > %(after_id)s ORDER BY s.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "subscriptions_views",
        "SELECT sv.* FROM subscriptions_views sv WHERE sv.id > %(after_id)s ORDER BY sv.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "subscriptions_workbooks",
        "SELECT sw.* FROM subscriptions_workbooks sw WHERE sw.id > %(after_id)s "
        "ORDER BY sw.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "data_alerts",
        "SELECT da.* FROM data_alerts da WHERE da.id > %(after_id)s ORDER BY da.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "data_alerts_recipients",
        "SELECT dr.* FROM data_alerts_recipients dr WHERE dr.id > %(after_id)s "
        "ORDER BY dr.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "comments",
        "SELECT c.id, c.comment, c.commentable_id, c.commentable_type, c.user_id, c.created_at, "
        "c.updated_at FROM comments c WHERE c.id > %(after_id)s ORDER BY c.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "workbook_sizes",
        "SELECT w.id, w.luid, w.name, w.site_id, w.project_id, w.size, w.data_engine_extracts, "
        "w.updated_at FROM workbooks w WHERE w.id > %(after_id)s ORDER BY w.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "datasource_sizes",
        "SELECT d.id, d.luid, d.name, d.site_id, d.parent_workbook_id, d.size, d.data_engine_extracts, "
        "d.updated_at FROM datasources d WHERE d.id > %(after_id)s ORDER BY d.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "views",
        "SELECT v.id, v.luid, v.name, v.repository_url, v.workbook_id, v.site_id "
        "FROM views v WHERE v.id > %(after_id)s ORDER BY v.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "users",
        # Repository user ids → REST LUIDs and names, to resolve alert and subscription owners.
        "SELECT u.id, u.luid, su.name, u.site_id FROM users u "
        "JOIN system_users su ON su.id = u.system_user_id "
        "WHERE u.id > %(after_id)s ORDER BY u.id LIMIT %(limit)s",
    ),
    RepoQuery(
        "extracts",
        "SELECT e.* FROM extracts e WHERE e.id > %(after_id)s ORDER BY e.id LIMIT %(limit)s",
    ),
)

SOURCE_NAMES = tuple(q.name for q in QUERIES)


@dataclass
class SourceStats:
    rows: int = 0
    pages: int = 0
    truncated: bool = False
    last_id: int = 0
    error: str | None = None


@dataclass
class RepositoryStats:
    sources: dict[str, SourceStats] = field(default_factory=dict)

    @property
    def errors(self) -> dict[str, str]:
        return {name: s.error for name, s in self.sources.items() if s.error}

    def to_dict(self) -> dict[str, Any]:
        return {name: s.__dict__ for name, s in self.sources.items()}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS repository_rows (
  server TEXT NOT NULL, source TEXT NOT NULL, row_id INTEGER NOT NULL, data TEXT NOT NULL,
  last_run INTEGER NOT NULL, PRIMARY KEY (server, source, row_id)
);
CREATE TABLE IF NOT EXISTS repository_checkpoints (
  server TEXT NOT NULL, source TEXT NOT NULL, last_id INTEGER NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (server, source)
);
CREATE TABLE IF NOT EXISTS repository_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, stats TEXT
);
"""


class RepositoryStore:
    """SQLite tables for repository rows; may share a file with the inventory."""

    def __init__(self, path: Path | str) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def start_run(self, server: str) -> int:
        cur = self.db.execute(
            "INSERT INTO repository_runs (server, started_at) VALUES (?, ?)", (server, _now())
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, stats: RepositoryStats) -> None:
        self.db.execute(
            "UPDATE repository_runs SET finished_at = ?, stats = ? WHERE id = ?",
            (_now(), json.dumps(stats.to_dict()), run_id),
        )
        self.db.commit()

    def last_id(self, server: str, source: str) -> int:
        row = self.db.execute(
            "SELECT last_id FROM repository_checkpoints WHERE server = ? AND source = ?", (server, source)
        ).fetchone()
        return int(row[0]) if row else 0

    def save_last_id(self, server: str, source: str, last_id: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO repository_checkpoints VALUES (?, ?, ?, ?)",
            (server, source, last_id, _now()),
        )

    def put_rows(self, server: str, source: str, rows: Iterable[dict[str, Any]], run_id: int) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO repository_rows VALUES (?, ?, ?, ?, ?)",
            [
                (
                    server,
                    source,
                    int(row["id"]),
                    json.dumps(row, sort_keys=True, default=_json_default),
                    run_id,
                )
                for row in rows
            ],
        )

    def rows(self, source: str) -> Iterator[dict[str, Any]]:
        for (data,) in self.db.execute(
            "SELECT data FROM repository_rows WHERE source = ? ORDER BY row_id", (source,)
        ):
            yield json.loads(data)

    def view_usage(self) -> dict[int, int]:
        """Total ``nviews`` per repository ``view_id`` from ``views_stats``."""
        totals: dict[int, int] = {}
        for row in self.rows("views_stats"):
            if row.get("view_id") is not None:
                view_id = int(row["view_id"])
                totals[view_id] = totals.get(view_id, 0) + int(row.get("nviews") or 0)
        return totals

    def view_usage_by_luid(self) -> dict[str, int]:
        """Total views per view LUID (``views_stats`` joined to ``views``)."""
        luids = {int(row["id"]): str(row["luid"]) for row in self.rows("views") if row.get("luid")}
        return {luids[vid]: n for vid, n in self.view_usage().items() if vid in luids}


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def harvest_repository(
    reader: RepositoryReader,
    store: RepositoryStore,
    sources: Iterable[str] = SOURCE_NAMES,
    since: datetime | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> RepositoryStats:
    """Copy each source into ``store``; a failing source is recorded and the next one runs.

    Incremental sources continue after the highest id stored by earlier runs. ``since`` bounds
    the history of time-stamped usage tables (default: 183 days ago).
    """
    by_name = {q.name: q for q in QUERIES}
    unknown = [name for name in sources if name not in by_name]
    if unknown:
        raise ValueError(f"unknown repository sources: {', '.join(unknown)}")
    if page_size <= 0 or max_rows <= 0:
        raise ValueError("page_size and max_rows must be positive")
    since = since or datetime.now(UTC) - timedelta(days=183)
    stats = RepositoryStats()
    run_id = store.start_run(reader.server)
    for name in sources:
        query = by_name[name]
        source_stats = stats.sources.setdefault(name, SourceStats())
        after_id = store.last_id(reader.server, name) if query.incremental else 0
        sql = ensure_select(query.sql)
        try:
            while True:
                limit = min(page_size, max_rows - source_stats.rows)
                if limit <= 0:
                    source_stats.truncated = True
                    break
                params: dict[str, Any] = {"after_id": after_id, "limit": limit}
                if query.uses_since:
                    params["since"] = since
                rows = reader.fetch(sql, params)
                if query.transform:
                    rows = [query.transform(dict(row)) for row in rows]
                if rows:
                    store.put_rows(reader.server, name, rows, run_id)
                    after_id = max(int(row["id"]) for row in rows)
                    if query.incremental:
                        store.save_last_id(reader.server, name, after_id)
                    store.db.commit()
                source_stats.rows += len(rows)
                source_stats.pages += 1
                source_stats.last_id = after_id
                if len(rows) < limit:
                    break
        except Exception as exc:
            store.db.commit()
            source_stats.error = f"{type(exc).__name__}: {exc}"
    store.finish_run(run_id, stats)
    return stats


@dataclass(frozen=True)
class RepositoryConnection:
    """How to reach the ``workgroup`` database as the ``readonly`` user."""

    host: str
    password: str = field(repr=False)
    port: int = 8060
    user: str = "readonly"
    dbname: str = "workgroup"
    sslmode: str = "prefer"

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> RepositoryConnection:
        missing = [n for n in ("TABLEAU_REPO_HOST", "TABLEAU_REPO_PASSWORD") if not environ.get(n)]
        if missing:
            raise ConfigError(f"missing environment variables: {', '.join(missing)}")
        try:
            port = int(environ.get("TABLEAU_REPO_PORT") or 8060)
        except ValueError:
            raise ConfigError("TABLEAU_REPO_PORT must be a number") from None
        return cls(
            host=environ["TABLEAU_REPO_HOST"],
            password=environ["TABLEAU_REPO_PASSWORD"],
            port=port,
            user=environ.get("TABLEAU_REPO_USER") or "readonly",
            dbname=environ.get("TABLEAU_REPO_DB") or "workgroup",
            sslmode=environ.get("TABLEAU_REPO_SSLMODE") or "prefer",
        )

    @property
    def label(self) -> str:
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.dbname}"


class PsycopgRepository:
    """:class:`RepositoryReader` over psycopg 3 (``tabfree-migrate[repository]``). Context manager.

    Unverified against a live Tableau repository.
    """

    def __init__(
        self, connection: RepositoryConnection, statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS
    ) -> None:
        self.server = connection.label
        self._connection = connection
        self._timeout = int(statement_timeout_ms)
        self._conn: Any = None

    def __enter__(self) -> Self:
        try:
            psycopg: Any = importlib.import_module("psycopg")
            rows: Any = importlib.import_module("psycopg.rows")
        except ImportError:
            raise RuntimeError(
                "repository harvest needs the optional extra: tabfree-migrate[repository]"
            ) from None
        c = self._connection
        self._conn = psycopg.connect(
            host=c.host,
            port=c.port,
            dbname=c.dbname,
            user=c.user,
            password=c.password,
            sslmode=c.sslmode,
            connect_timeout=15,
            application_name="tabfree-migrate",
            options=f"-c statement_timeout={self._timeout} -c default_transaction_read_only=on",
            row_factory=rows.dict_row,
        )
        self._conn.read_only = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            self._conn.close()

    def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(ensure_select(sql), dict(params))
                return [dict(row) for row in cur.fetchall()]
        finally:
            # End the read-only transaction so a long harvest never holds a snapshot open.
            self._conn.rollback()


_NAMED = re.compile(r"%\((\w+)\)s")


class SqliteRepository:
    """:class:`RepositoryReader` over SQLite with the same SQL; for tests and local fixtures."""

    def __init__(self, db: sqlite3.Connection, server: str = "sqlite://fixture") -> None:
        self.server = server
        self._db = db

    def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        converted = _NAMED.sub(r":\1", ensure_select(sql))
        values = {k: v.isoformat() if isinstance(v, datetime | date) else v for k, v in params.items()}
        cur = self._db.execute(converted, values)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
