from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.repository import (
    QUERIES,
    SOURCE_NAMES,
    PsycopgRepository,
    RepositoryConnection,
    RepositoryStore,
    SqliteRepository,
    ensure_select,
    harvest_repository,
    redact_uri,
)
from tabfree_migrate.tableau import ConfigError

_FIXTURE = """
CREATE TABLE views_stats (id INTEGER, view_id INTEGER, user_id INTEGER, site_id INTEGER,
  nviews INTEGER, time TEXT);
CREATE TABLE http_requests (id INTEGER, controller TEXT, action TEXT, http_request_uri TEXT,
  currentsheet TEXT, status INTEGER, created_at TEXT, completed_at TEXT, user_id INTEGER,
  site_id INTEGER, vizql_session TEXT, remote_ip TEXT);
CREATE TABLE historical_events (id INTEGER, created_at TEXT, historical_event_type_id INTEGER,
  is_failure INTEGER, duration_in_ms INTEGER, hist_actor_user_id INTEGER, hist_view_id INTEGER,
  hist_workbook_id INTEGER, hist_datasource_id INTEGER, hist_actor_site_id INTEGER);
CREATE TABLE historical_event_types (type_id INTEGER, name TEXT, action_type TEXT);
CREATE TABLE hist_users (id INTEGER, user_id INTEGER);
CREATE TABLE hist_views (id INTEGER, view_id INTEGER);
CREATE TABLE hist_workbooks (id INTEGER, workbook_id INTEGER);
CREATE TABLE hist_datasources (id INTEGER, datasource_id INTEGER);
CREATE TABLE hist_sites (id INTEGER, site_id INTEGER);
CREATE TABLE subscriptions (id INTEGER, subject TEXT, user_id INTEGER, schedule_id INTEGER);
CREATE TABLE subscriptions_views (id INTEGER, subscription_id INTEGER, repository_url TEXT);
CREATE TABLE subscriptions_workbooks (id INTEGER, subscription_id INTEGER, repository_url TEXT);
CREATE TABLE data_alerts (id INTEGER, title TEXT, view_id INTEGER, threshold REAL);
CREATE TABLE data_alerts_recipients (id INTEGER, data_alert_id INTEGER, recipient_id INTEGER);
CREATE TABLE comments (id INTEGER, comment TEXT, commentable_id INTEGER, commentable_type TEXT,
  user_id INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE workbooks (id INTEGER, luid TEXT, name TEXT, site_id INTEGER, project_id INTEGER,
  size INTEGER, data_engine_extracts INTEGER, updated_at TEXT);
CREATE TABLE datasources (id INTEGER, luid TEXT, name TEXT, site_id INTEGER,
  parent_workbook_id INTEGER, size INTEGER, data_engine_extracts INTEGER, updated_at TEXT);
CREATE TABLE views (id INTEGER, luid TEXT, name TEXT, repository_url TEXT, workbook_id INTEGER,
  site_id INTEGER);
CREATE TABLE extracts (id INTEGER, workbook_id INTEGER, datasource_id INTEGER, descriptor TEXT);
CREATE TABLE users (id INTEGER, luid TEXT, site_id INTEGER, system_user_id INTEGER);
CREATE TABLE system_users (id INTEGER, name TEXT);
"""


def _fixture() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(_FIXTURE)
    db.executemany(
        "INSERT INTO views_stats VALUES (?, ?, 1, 1, ?, '2026-10-01')", [(1, 10, 5), (2, 10, 7), (3, 11, 1)]
    )
    db.executemany(
        "INSERT INTO views VALUES (?, ?, ?, '', 1, 1)", [(10, "v-10", "Sales"), (11, "v-11", "Ops")]
    )
    db.executemany(
        "INSERT INTO http_requests VALUES (?, 'views', 'show', ?, 'Sales', 200, ?, ?, 1, 1, 's', '10.0.0.1')",
        [
            (1, "/views/Sales/Sheet1?vf_Region=East&:embed=y", "2020-01-01T00:00:00", "2020-01-01"),
            (2, "/trusted/abcdef-ticket/views/Sales/Sheet1", "2026-10-02T00:00:00", "2026-10-02"),
            (3, "/views/Ops/Overview#frag", "2026-10-03T00:00:00", "2026-10-03"),
        ],
    )
    db.execute("INSERT INTO historical_event_types VALUES (84, 'Access View', 'Access')")
    db.execute("INSERT INTO hist_views VALUES (500, 10)")
    db.execute("INSERT INTO hist_users VALUES (600, 1)")
    db.execute(
        "INSERT INTO historical_events VALUES (1, '2026-10-05T00:00:00', 84, 0, 12, 600, 500, "
        "NULL, NULL, NULL)"
    )
    db.execute("INSERT INTO subscriptions VALUES (1, 'Weekly sales', 1, 3)")
    db.execute("INSERT INTO data_alerts VALUES (1, 'Sales drop', 10, 1000.5)")
    db.execute("INSERT INTO comments VALUES (1, 'Check Q3', 10, 'View', 1, '2026-01-01', '2026-01-01')")
    db.execute("INSERT INTO workbooks VALUES (1, 'wb-1', 'Superstore', 1, 2, 123456, 1, '2026-01-01')")
    db.execute("INSERT INTO datasources VALUES (1, 'ds-1', 'Orders', 1, NULL, 999, 1, '2026-01-01')")
    db.execute("INSERT INTO extracts VALUES (1, 1, NULL, 'x')")
    db.commit()
    return db


_SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def test_every_query_is_a_parameterised_paged_select() -> None:
    for query in QUERIES:
        assert ensure_select(query.sql) == query.sql
        assert "%(after_id)s" in query.sql
        assert query.sql.rstrip().endswith("LIMIT %(limit)s")
        assert query.uses_since == ("%(since)s" in query.sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM views",
        "SELECT 1; DROP TABLE views",
        "WITH x AS (DELETE FROM views RETURNING *) SELECT * FROM x",
        "select pg_sleep(1) from views for update",
        "  update views set name = 'x'",
    ],
)
def test_writes_are_rejected(sql: str) -> None:
    with pytest.raises(ValueError, match="repository quer"):
        ensure_select(sql)


def test_redact_uri_drops_query_strings_and_tickets() -> None:
    assert redact_uri("/views/A/B?vf_Region=East") == "/views/A/B"
    assert redact_uri("/trusted/T0KEN/views/A/B") == "/trusted/REDACTED/views/A/B"
    assert redact_uri(None) is None


def test_harvest_copies_every_source(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "inventory.db")
    stats = harvest_repository(SqliteRepository(_fixture()), store, since=_SINCE)
    assert stats.errors == {}
    assert set(stats.sources) == set(SOURCE_NAMES)
    assert stats.sources["views_stats"].rows == 3
    assert stats.sources["http_requests"].rows == 2
    uris = [r["http_request_uri"] for r in store.rows("http_requests")]
    assert uris == ["/trusted/REDACTED/views/Sales/Sheet1", "/views/Ops/Overview"]
    assert all("remote_ip" not in r for r in store.rows("http_requests"))
    event = next(store.rows("historical_events"))
    assert (event["event_type"], event["view_id"], event["actor_user_id"]) == ("Access View", 10, 1)
    assert next(store.rows("workbook_sizes"))["size"] == 123456
    assert next(store.rows("data_alerts"))["title"] == "Sales drop"
    assert next(store.rows("comments"))["comment"] == "Check Q3"
    assert store.view_usage() == {10: 12, 11: 1}
    assert store.view_usage_by_luid() == {"v-10": 12, "v-11": 1}


def test_incremental_sources_continue_after_the_last_id(tmp_path: Path) -> None:
    db = _fixture()
    store = RepositoryStore(tmp_path / "r.db")
    harvest_repository(SqliteRepository(db), store, ["http_requests", "views_stats"], since=_SINCE)
    db.execute(
        "INSERT INTO http_requests VALUES (4, 'views', 'show', '/v', 'S', 200, '2026-11-01', "
        "'', 1, 1, 's', '')"
    )
    db.execute("UPDATE views_stats SET nviews = 50 WHERE id = 1")
    seen: list[Mapping[str, Any]] = []

    class Spy(SqliteRepository):
        def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
            seen.append(params)
            return super().fetch(sql, params)

    stats = harvest_repository(Spy(db), store, ["http_requests", "views_stats"], since=_SINCE)
    assert stats.sources["http_requests"].rows == 1
    assert seen[0]["after_id"] == 3
    assert seen[1]["after_id"] == 0  # views_stats is re-read in full: its counters change
    assert store.view_usage()[10] == 57


def test_paging_and_row_cap(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "r.db")
    stats = harvest_repository(SqliteRepository(_fixture()), store, ["views_stats"], page_size=2)
    assert (stats.sources["views_stats"].rows, stats.sources["views_stats"].pages) == (3, 2)
    capped = harvest_repository(SqliteRepository(_fixture()), store, ["views_stats"], page_size=1, max_rows=2)
    assert capped.sources["views_stats"].rows == 2
    assert capped.sources["views_stats"].truncated


def test_missing_table_is_recorded_and_harvest_continues(tmp_path: Path) -> None:
    db = _fixture()
    db.execute("DROP TABLE data_alerts")
    stats = harvest_repository(SqliteRepository(db), RepositoryStore(tmp_path / "r.db"), since=_SINCE)
    assert list(stats.errors) == ["data_alerts"]
    assert "no such table" in stats.errors["data_alerts"]
    assert stats.sources["comments"].rows == 1


def test_bad_arguments(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "r.db")
    with pytest.raises(ValueError, match="unknown repository sources: passwords"):
        harvest_repository(SqliteRepository(_fixture()), store, ["passwords"])
    with pytest.raises(ValueError, match="positive"):
        harvest_repository(SqliteRepository(_fixture()), store, page_size=0)


def test_connection_from_env_hides_password() -> None:
    with pytest.raises(ConfigError, match="TABLEAU_REPO_HOST, TABLEAU_REPO_PASSWORD"):
        RepositoryConnection.from_env({})
    with pytest.raises(ConfigError, match="number"):
        RepositoryConnection.from_env(
            {"TABLEAU_REPO_HOST": "h", "TABLEAU_REPO_PASSWORD": "p", "TABLEAU_REPO_PORT": "x"}
        )
    conn = RepositoryConnection.from_env(
        {"TABLEAU_REPO_HOST": "tableau-db", "TABLEAU_REPO_PASSWORD": "pw-123"}
    )
    assert (conn.port, conn.user, conn.dbname) == (8060, "readonly", "workgroup")
    assert "pw-123" not in repr(conn)
    assert conn.label == "postgresql://readonly@tableau-db:8060/workgroup"


def test_psycopg_repository_opens_read_only_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: dict[str, Any] = {}

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: dict[str, Any]) -> None:
            opened["executed"] = (sql, params)

        def fetchall(self) -> list[dict[str, Any]]:
            return [{"id": 1}]

    class Conn:
        read_only = False

        def cursor(self) -> Cursor:
            return Cursor()

        def rollback(self) -> None:
            opened["rolled_back"] = True

        def close(self) -> None:
            opened["closed"] = True

    class Psycopg:
        @staticmethod
        def connect(**kwargs: Any) -> Conn:
            opened.update(kwargs)
            return Conn()

    class Rows:
        dict_row = object()

    modules = {"psycopg": Psycopg, "psycopg.rows": Rows}
    monkeypatch.setattr(importlib, "import_module", lambda name: modules[name])
    conn = RepositoryConnection(host="db", password="pw")
    with PsycopgRepository(conn, statement_timeout_ms=5000) as repo:
        assert repo._conn.read_only is True
        assert repo.fetch("SELECT id FROM views WHERE id > %(after_id)s", {"after_id": 0}) == [{"id": 1}]
        with pytest.raises(ValueError, match="SELECT"):
            repo.fetch("DELETE FROM views", {})
    assert "statement_timeout=5000" in opened["options"]
    assert "default_transaction_read_only=on" in opened["options"]
    assert (opened["user"], opened["dbname"], opened["port"]) == ("readonly", "workgroup", 8060)
    assert opened["rolled_back"]
    assert opened["closed"]


def test_psycopg_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with (
        pytest.raises(RuntimeError, match=r"tabfree-migrate\[repository\]"),
        PsycopgRepository(RepositoryConnection(host="db", password="pw")),
    ):
        pass
