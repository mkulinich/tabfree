from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tabfree_migrate import cli
from tabfree_migrate.report import build, connection_location, connections_of, to_markdown, write_report


def workbook(
    formulas: list[str], conn: dict[str, Any], *, blended: bool = False, extract: bool = False
) -> dict[str, Any]:
    ds = {
        "name": "federated.1",
        "kind": "embedded",
        "caption": "Orders",
        "connection": conn,
        "columns": [{"name": f"[c{i}]", "calculation": {"formula": f}} for i, f in enumerate(formulas)],
    }
    if extract:
        ds["extract"] = {"enabled": True}
    ws = {"name": "Sheet 1", "datasources": ["federated.1", "federated.2"] if blended else ["federated.1"]}
    return {
        "datasources": [{"name": "Parameters", "kind": "parameters", "columns": []}, ds],
        "worksheets": [ws],
    }


def test_connection_locations() -> None:
    assert connection_location("hyper", None) == "file"
    assert connection_location("redshift", "x") == "aws"
    assert connection_location("postgres", "db.abc.us-east-1.rds.amazonaws.com") == "aws"
    assert connection_location("sqlserver", "sql01.corp.local") == "outside_aws"
    conns = connections_of("wb", workbook([], {"class": "postgres", "server": "pg.corp"}, extract=True))
    assert [(c.cls, c.location, c.extract) for c in conns] == [("postgres", "outside_aws", True)]


def test_triage_reasons_blockers_and_retirement() -> None:
    irs = [
        ("a/simple.twb", workbook(["SUM([Sales])"], {"class": "hyper"})),
        ("a/blended.twb", workbook([], {"class": "textscan"}, blended=True)),
        ("a/regex.twb", workbook(["REGEXP_MATCH([Name], 'x')", "RAWSQL_INT('1')"], {"class": "redshift"})),
        ("a/rls.twb", workbook(["[Manager] = USERNAME()"], {"class": "hyper"})),
        ("a/unused.twb", workbook([], {"class": "hyper"})),
    ]
    workbooks, connections, estate = build(irs, usage={"unused": 0, "simple": 40})
    by = {w.name: w for w in workbooks}
    assert by["simple"].triage == "migrate"
    assert by["simple"].views_180d == 40
    assert by["blended"].triage == "rebuild"
    assert "data blending" in by["blended"].reasons[0]
    assert by["regex"].triage == "rebuild"  # RAWSQL
    assert by["regex"].blockers == ["compiler support for REGEXP_MATCH, RAWSQL_INT"]
    assert by["unused"].triage == "retire"
    assert by["rls"].census.totals["user_functions"] == 1
    assert estate.totals["calculated_fields"] == 4
    assert len(connections) == 5

    md = to_markdown(workbooks, connections, estate)
    assert "**Triage:** 2 migrate, 2 rebuild, 1 retire." in md
    assert "| REGEXP_MATCH | 1 |" in md
    assert "1 already in AWS" in md


def test_markdown_cells_are_escaped(tmp_path: Path) -> None:
    workbooks, connections, estate = build([("x/a|b.twb", workbook([], {"class": "hyper"}))])
    md_path, json_path = write_report(tmp_path, workbooks, connections, estate)
    assert "| a\\|b |" in md_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["workbooks"][0]["name"] == "a|b"
    assert data["triage"] == {"migrate": 1}


def test_report_command_over_the_corpus(tmp_path: Path) -> None:
    corpus = Path(__file__).resolve().parents[3] / "corpus" / "workbooks" / "tableau__connector-plugin-sdk"
    usage = tmp_path / "usage.csv"
    usage.write_text("workbook,views_180d\nTDVT Results,0\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app, ["report", str(corpus), "--out", str(tmp_path / "out"), "--usage", str(usage)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    tdvt = next(w for w in data["workbooks"] if w["name"] == "TDVT Results")
    assert tdvt["triage"] == "retire"
