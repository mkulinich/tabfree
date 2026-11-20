from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.census import census_of, functions_in
from tabfree_migrate.twb import TableauFileError, parse_file
from tabfree_schemas import SchemaKind, schema_errors

REPO_ROOT = Path(__file__).resolve().parents[3]
TDVT = REPO_ROOT / "corpus/workbooks/tableau__connector-plugin-sdk/tdvt/TDVT Results.twb"


def corpus_entries() -> list[dict[str, Any]]:
    manifest = REPO_ROOT / "corpus" / "manifest.json"
    if not manifest.exists():
        return []
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    return [f for f in files if f.get("kind") in {"twb", "twbx", "tds", "tdsx"} and f.get("path")]


@pytest.mark.parametrize("entry", corpus_entries(), ids=lambda e: e["path"].split("/", 2)[-1])
def test_every_corpus_file_parses_to_valid_ir_or_is_expected_invalid(entry: dict[str, Any]) -> None:
    path = REPO_ROOT / entry["path"]
    if entry.get("expect_invalid"):
        with pytest.raises(TableauFileError):
            parse_file(path)
        return
    ir = parse_file(path)
    assert schema_errors(SchemaKind.TABLEAU_IR, ir) == []
    assert ir["kind"] == ("datasource" if entry["kind"] in {"tds", "tdsx"} else "workbook")


@pytest.mark.skipif(not TDVT.exists(), reason="corpus not present")
def test_tdvt_results_workbook_structure() -> None:
    ir = parse_file(TDVT)
    assert ir["source"]["sourceBuild"].startswith("2024.2.0")
    names = {w["name"] for w in ir["worksheets"]}
    assert {"Scorecard", "Details"} <= names
    scorecard = next(w for w in ir["worksheets"] if w["name"] == "Scorecard")
    assert scorecard["cols"]["instances"] == ["[textscan.1gc4mna00d9z6u19vsqbk09t94y2].[none:Passed:nk]"]
    assert scorecard["panes"][0]["encodings"]["text"]
    assert [d["name"] for d in ir["dashboards"]] == ["TDVT Test Results"]
    params = {p.get("caption") for p in ir["parameters"]}
    assert "Error Sheet Swap" in params
    action = next(a for a in ir["actions"] if a["name"] == "[Action1]")
    assert (action["type"], action["trigger"]) == ("filter", "select")
    # credentials are never captured, even empty ones
    text = json.dumps(ir)
    assert "password" not in text


def test_feature_flag_elements_are_normalised(tmp_path: Path) -> None:
    twb = tmp_path / "flags.twb"
    twb.write_text(
        "<?xml version='1.0'?><workbook version='18.1'><datasources>"
        "<datasource name='federated.1'><connection class='federated'>"
        "<_.fcp.ObjectModelEncapsulateLegacy.false...relation type='table' name='Orders' table='[Orders]'/>"
        "</connection></datasource></datasources></workbook>",
        encoding="utf-8",
    )
    ir = parse_file(twb)
    assert ir["datasources"][0]["relation"] == {"type": "table", "name": "Orders", "table": "[Orders]"}


def test_packaged_workbook_and_errors(tmp_path: Path) -> None:
    twbx = tmp_path / "book.twbx"
    with zipfile.ZipFile(twbx, "w") as archive:
        archive.writestr("book.twb", "<workbook version='18.1'/>")
    ir = parse_file(twbx)
    assert ir["source"]["packaged"] is True
    with pytest.raises(TableauFileError, match="unsupported file type"):
        parse_file(tmp_path / "notes.txt")
    bad = tmp_path / "bad.twbx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(TableauFileError, match="not a valid package"):
        parse_file(bad)


def test_functions_ignore_field_names_and_strings() -> None:
    formula = 'IF ISNULL([Name (Owner)]) THEN "SUM(x)" ELSE STR({FIXED [Region] : SUM([Sales])}) END'
    assert functions_in(formula) == ["ISNULL", "STR", "SUM"]


@pytest.mark.skipif(not TDVT.exists(), reason="corpus not present")
def test_census_counts_and_scores() -> None:
    census = census_of(parse_file(TDVT))
    assert census.totals["worksheets"] >= 2
    assert census.totals["dashboards"] == 1
    assert census.actions["filter"] >= 1
    assert census.connections["textscan"] >= 1
    assert census.score() > 0
