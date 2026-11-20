from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from tabfree_migrate import __version__, cli
from tabfree_migrate.smoke import format_report, run_smoke
from tabfree_migrate.tableau import (
    COUNTED_ENDPOINTS,
    ConfigError,
    TableauConnection,
    TableauReader,
)
from tabfree_migrate.testing import FakeReader


def test_run_smoke_collects_counts_versions_and_samples(connection: TableauConnection) -> None:
    report = run_smoke(FakeReader(), connection, sample=2)
    assert report.server.rest_api_version == "3.30"
    assert set(report.counts) == set(COUNTED_ENDPOINTS)
    assert report.counts["workbooks"] == 340
    assert report.site_count == 2
    assert report.metadata_api is True
    assert report.samples == {
        "projects": ["projects-1", "projects-2"],
        "workbooks": ["workbooks-1", "workbooks-2"],
    }


def test_report_explains_missing_permissions_and_metadata(connection: TableauConnection) -> None:
    reader = FakeReader(counts={"users": None}, sites=None, metadata=False)
    text = format_report(run_smoke(reader, connection))
    assert "Sites        n/a (not permitted)" in text
    assert "Users        n/a (not permitted)" in text
    assert "NOT available" in text


def test_secret_never_appears_in_reports_or_repr(connection: TableauConnection, secret: str) -> None:
    report = run_smoke(FakeReader(), connection, sample=3)
    assert secret not in format_report(report)
    assert secret not in json.dumps(report.to_dict())
    assert secret not in repr(connection)


def test_connection_from_env_validates_input() -> None:
    with pytest.raises(ConfigError, match="TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET"):
        TableauConnection.from_env({"TABLEAU_SERVER_URL": "https://t.example.com"})
    with pytest.raises(ConfigError, match="must start with https://"):
        TableauConnection.from_env(
            {
                "TABLEAU_SERVER_URL": "t.example.com",
                "TABLEAU_PAT_NAME": "n",
                "TABLEAU_PAT_SECRET": "s",
            }
        )
    conn = TableauConnection.from_env(
        {
            "TABLEAU_SERVER_URL": "https://t.example.com/",
            "TABLEAU_PAT_NAME": "n",
            "TABLEAU_PAT_SECRET": "s",
        }
    )
    assert conn.server_url == "https://t.example.com"
    assert conn.site == ""


# --- CLI -----------------------------------------------------------------------------------------


@pytest.fixture
def fake_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def open_fake(_connection: TableauConnection) -> Iterator[TableauReader]:
        yield FakeReader()

    monkeypatch.setattr(cli, "open_reader", open_fake)


@pytest.mark.usefixtures("tableau_env", "fake_reader")
def test_smoke_command_prints_report(secret: str) -> None:
    result = CliRunner().invoke(cli.app, ["smoke", "--sample", "2"])
    assert result.exit_code == 0, result.output
    assert "Server       https://tableau.example.com" in result.output
    assert "REST API     3.30" in result.output
    assert "  - workbooks-2" in result.output
    assert secret not in result.output


@pytest.mark.usefixtures("tableau_env", "fake_reader")
def test_smoke_command_json() -> None:
    result = CliRunner().invoke(cli.app, ["smoke", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["site"] == "finance"
    assert data["counts"]["groups"] == 31


def test_smoke_command_without_configuration_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TABLEAU_SERVER_URL", "TABLEAU_SITE", "TABLEAU_PAT_NAME", "TABLEAU_PAT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(cli.app, ["smoke"])
    assert result.exit_code == 2
    assert "missing environment variables" in result.output


def test_version_command() -> None:
    result = CliRunner().invoke(cli.app, ["version"])
    assert result.output.strip() == f"tabfree-migrate {__version__}"
