"""Corpus files against Tableau's official XSDs, with an explicit list of known deviations (P2.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.twb import TableauFileError

pytest.importorskip("xmlschema")

from tabfree_migrate.xsd import DEVIATIONS, classify, validate_file, xsd_path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = REPO_ROOT / "third_party" / "tableau-document-schemas" / "schemas"
KNOWN = json.loads((Path(__file__).parent / "xsd_known_deviations.json").read_text(encoding="utf-8"))["files"]
CODES = {d.code for d in DEVIATIONS}


def corpus_entries() -> list[dict[str, Any]]:
    manifest = REPO_ROOT / "corpus" / "manifest.json"
    if not manifest.exists():
        return []
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    return [
        f
        for f in files
        if f.get("kind") in {"twb", "twbx", "tds", "tdsx"} and f.get("path") and not f.get("expect_invalid")
    ]


@pytest.mark.skipif(not SCHEMAS.exists(), reason="vendored XSDs not present")
@pytest.mark.parametrize("entry", corpus_entries(), ids=lambda e: e["path"].split("/", 2)[-1])
def test_corpus_file_matches_its_known_xsd_deviations(entry: dict[str, Any]) -> None:
    issues = validate_file(REPO_ROOT / entry["path"], SCHEMAS)
    unclassified = [f"{i.path}: {i.reason}" for i in issues if i.code == "unclassified"]
    assert unclassified == [], "new kind of XSD deviation: classify it in tabfree_migrate.xsd.DEVIATIONS"
    assert sorted({i.code for i in issues}) == KNOWN.get(entry["path"], []), (
        "XSD deviations changed: update tests/xsd_known_deviations.json if this is expected"
    )


def test_known_deviation_list_is_honest() -> None:
    paths = {e["path"] for e in corpus_entries()}
    if paths:
        assert set(KNOWN) <= paths, "known deviations listed for files that are not in the corpus"
    for codes in KNOWN.values():
        assert codes
        assert set(codes) <= CODES
    assert len(CODES) == len(DEVIATIONS), "deviation codes must be unique"


def test_xsd_is_chosen_by_document_version() -> None:
    assert xsd_path(SCHEMAS, "26.2").name == "twb_2026.2.0.xsd"
    assert xsd_path(SCHEMAS, "26.3").name == "twb_2026.3.0.xsd"
    assert xsd_path(SCHEMAS, "18.1").name == "twb_2026.1.0.xsd"
    assert xsd_path(SCHEMAS, None).name == "twb_2026.1.0.xsd"


def test_classification() -> None:
    assert (
        classify(
            "workbook", "The content of element 'workbook' is not complete. Tag 'explain-data' expected."
        )
        == "missing-explain-data"
    )
    assert classify("layout", "'dim-percentage' attribute not allowed for element") == "legacy-schema-layout"
    assert classify("zone", "something new") == "unclassified"


@pytest.mark.skipif(not SCHEMAS.exists(), reason="vendored XSDs not present")
def test_valid_minimal_datasource_and_deviation_paths(tmp_path: Path) -> None:
    tds = tmp_path / "ok.tds"
    tds.write_text(
        "<?xml version='1.0'?><datasource inline='true' version='26.1'>"
        "<connection class='hyper' dbname='x.hyper'/>"
        "<column datatype='integer' name='[n]' role='measure' type='quantitative'/></datasource>",
        encoding="utf-8",
    )
    assert validate_file(tds, SCHEMAS) == []
    twb = tmp_path / "old.twb"
    twb.write_text("<?xml version='1.0'?><workbook version='18.1'><worksheets/></workbook>", encoding="utf-8")
    codes = {i.code for i in validate_file(twb, SCHEMAS)}
    assert "missing-source-build" in codes


def test_untrusted_documents_with_entities_are_refused(tmp_path: Path) -> None:
    evil = tmp_path / "evil.twb"
    evil.write_text(
        "<?xml version='1.0'?><!DOCTYPE workbook [<!ENTITY x SYSTEM 'file:///etc/passwd'>]>"
        "<workbook version='26.1'>&x;</workbook>",
        encoding="utf-8",
    )
    with pytest.raises(TableauFileError):
        validate_file(evil, SCHEMAS)
