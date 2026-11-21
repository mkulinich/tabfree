from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tabfree_migrate import cli
from tabfree_migrate.blobstore import load_json, sink_for
from tabfree_migrate.inventory import InventoryStore, Item
from tabfree_migrate.metadata import MetadataStore
from tabfree_migrate.repository import RepositoryConnection, RepositoryStore, SqliteRepository
from tabfree_migrate.tableau import TableauAccessError, TableauConnection
from tabfree_migrate.testing import FakeDownloader, FakeMetadataClient, FakeViewExporter

runner = CliRunner()

_REPO_PASSWORD = "repo-pw-never-print"


def _inventory(path: Path) -> None:
    store = InventoryStore(path)
    run = store.start_run("https://tableau.example.com", "finance")
    store.upsert("s", "finance", Item("workbooks", "w1", "Superstore", "2026-02-01T00:00:00Z", {}), run)
    store.upsert("s", "finance", Item("datasources", "d1", "Orders", "2026-02-02T00:00:00Z", {}), run)
    store.upsert("s", "finance", Item("views", "v1", "Sales", None, {"workbook_id": "w1"}), run)
    store.upsert("s", "finance", Item("views", "v2", "Ops", None, {"workbook_id": "w1"}), run)
    store.db.commit()
    store.close()


@pytest.fixture
def repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLEAU_REPO_HOST", "tableau-db.internal")
    monkeypatch.setenv("TABLEAU_REPO_PASSWORD", _REPO_PASSWORD)


@pytest.mark.usefixtures("tableau_env")
def test_harvest_metadata_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    nodes = {"publishedDatasourcesConnection": [{"id": "p1", "name": "Orders", "hasUserReference": True}]}

    @contextmanager
    def fake(connection: TableauConnection) -> Iterator[FakeMetadataClient]:
        yield FakeMetadataClient(nodes, broken={"sheetsConnection"})

    monkeypatch.setattr(cli, "metadata_client", fake)
    db = tmp_path / "inventory.db"
    result = runner.invoke(cli.app, ["harvest-metadata", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "publishedDatasources   1 of 1 (1 pages)" in result.output
    assert "sheets" in result.output
    assert "ERROR" in result.output
    assert secret not in result.output
    assert MetadataStore(db).rls_datasources()[0]["name"] == "Orders"

    bad = runner.invoke(cli.app, ["harvest-metadata", "--db", str(db), "--entity", "nope"])
    assert bad.exit_code == 2


@pytest.mark.usefixtures("tableau_env")
def test_sign_in_failure_exits_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    @contextmanager
    def refused(connection: TableauConnection) -> Iterator[Any]:
        raise TableauAccessError("sign-in failed: 401")
        yield  # pragma: no cover

    monkeypatch.setattr(cli, "metadata_client", refused)
    result = runner.invoke(cli.app, ["harvest-metadata", "--db", str(tmp_path / "m.db")])
    assert result.exit_code == 1
    assert "sign-in failed" in result.output
    assert secret not in result.output


def test_commands_need_tableau_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TABLEAU_SERVER_URL",
        "TABLEAU_PAT_NAME",
        "TABLEAU_PAT_SECRET",
        "TABLEAU_REPO_HOST",
        "TABLEAU_REPO_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    for args in (
        ["harvest-metadata"],
        ["download", "--out", str(tmp_path)],
        ["capture", "--out", str(tmp_path)],
        ["harvest-repository"],
    ):
        result = runner.invoke(cli.app, [*args, "--db", str(tmp_path / "x.db")])
        assert result.exit_code == 2, args
        assert "missing environment variables" in result.output


@pytest.mark.usefixtures("repo_env")
def test_harvest_repository_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = sqlite3.connect(":memory:")
    fixture.execute(
        "CREATE TABLE comments (id INTEGER, comment TEXT, commentable_id INTEGER, "
        "commentable_type TEXT, user_id INTEGER, created_at TEXT, updated_at TEXT)"
    )
    fixture.execute("INSERT INTO comments VALUES (1, 'hi', 1, 'View', 1, '2026-01-01', '2026-01-01')")
    opened: list[RepositoryConnection] = []

    @contextmanager
    def fake(connection: RepositoryConnection, timeout_ms: int) -> Iterator[SqliteRepository]:
        opened.append(connection)
        assert timeout_ms == 30_000
        yield SqliteRepository(fixture)

    monkeypatch.setattr(cli, "repository_reader", fake)
    db = tmp_path / "inventory.db"
    result = runner.invoke(
        cli.app,
        [
            "harvest-repository",
            "--db",
            str(db),
            "--source",
            "comments",
            "--source",
            "views_stats",
            "--statement-timeout",
            "30",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "comments                 1" in result.output
    assert "views_stats" in result.output
    assert "no such table" in result.output
    assert _REPO_PASSWORD not in result.output
    assert opened[0].host == "tableau-db.internal"
    assert next(RepositoryStore(db).rows("comments"))["comment"] == "hi"


@pytest.mark.usefixtures("repo_env")
def test_harvest_repository_connection_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def refused(connection: RepositoryConnection, timeout_ms: int) -> Iterator[Any]:
        raise RuntimeError("repository harvest needs the optional extra: tabfree-migrate[repository]")
        yield  # pragma: no cover

    monkeypatch.setattr(cli, "repository_reader", refused)
    result = runner.invoke(cli.app, ["harvest-repository", "--db", str(tmp_path / "r.db")])
    assert result.exit_code == 1
    assert "[repository]" in result.output
    assert _REPO_PASSWORD not in result.output


@pytest.mark.usefixtures("tableau_env")
def test_download_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "inventory.db"
    _inventory(db)
    downloader = FakeDownloader(
        {
            ("workbooks", "w1", False): b"PK\x03\x04wb",
            ("workbooks", "w1", True): b"PK\x03\x04wb+extract",
            ("datasources", "d1", False): b"PK\x03\x04ds",
        }
    )

    @contextmanager
    def fake(connection: TableauConnection) -> Iterator[FakeDownloader]:
        yield downloader

    monkeypatch.setattr(cli, "content_downloader", fake)
    out = tmp_path / "raw"
    args = ["download", "--db", str(db), "--out", str(out), "--with-extract", "w1"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "downloaded 3, skipped 0, failed 0" in result.output
    entries = load_json(sink_for(str(out)), "downloads/manifest.json")["entries"]
    assert set(entries) == {
        "workbooks/w1/no-extract",
        "datasources/d1/no-extract",
        "workbooks/w1/with-extract",
    }
    assert "skipped 3" in runner.invoke(cli.app, args).output

    empty = runner.invoke(cli.app, ["download", "--db", str(tmp_path / "none.db"), "--out", str(out)])
    assert empty.exit_code == 1
    assert "run `tabfree-migrate crawl` first" in empty.output


@pytest.mark.usefixtures("tableau_env")
def test_capture_command_uses_repository_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "inventory.db"
    _inventory(db)
    repo = RepositoryStore(db)
    repo.put_rows("pg", "views", [{"id": 1, "luid": "v1"}, {"id": 2, "luid": "v2"}], 1)
    repo.put_rows("pg", "views_stats", [{"id": 1, "view_id": 2, "nviews": 50}], 1)
    repo.db.commit()
    repo.close()
    exporter = FakeViewExporter()

    @contextmanager
    def fake(connection: TableauConnection) -> Iterator[FakeViewExporter]:
        yield exporter

    monkeypatch.setattr(cli, "view_exporter", fake)
    perms = tmp_path / "perms.json"
    perms.write_text(json.dumps({"default": [{"label": "east", "filters": {"Region": "East"}}]}))
    out = tmp_path / "gt"
    result = runner.invoke(
        cli.app,
        [
            "capture",
            "--db",
            str(db),
            "--out",
            str(out),
            "--format",
            "png",
            "--permutations",
            str(perms),
            "--top",
            "1",
            "--viz-width",
            "1200",
            "--viz-height",
            "800",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "captured 3, skipped 0, failed 0" in result.output
    # v2 has the usage, so it is the one with a permutation
    assert [(luid, params.get("vf_Region")) for luid, _fmt, params in exporter.calls] == [
        ("v2", None),
        ("v1", None),
        ("v2", "East"),
    ]
    assert exporter.calls[0][2]["vizWidth"] == "1200"

    bad = runner.invoke(cli.app, ["capture", "--db", str(db), "--out", str(out), "--format", "gif"])
    assert bad.exit_code == 2
    perms.write_text("[]")
    bad = runner.invoke(
        cli.app, ["capture", "--db", str(db), "--out", str(out), "--permutations", str(perms)]
    )
    assert bad.exit_code == 2
    assert "bad permutations file" in bad.output
