from __future__ import annotations

from pathlib import Path

import pytest

from tabfree_migrate.extract_format import ExtractFormat, detect_format, sniff
from tabfree_migrate.extract_types import (
    GEOGRAPHY_SRID,
    TYPE_RULES,
    ParityKind,
    is_binary_collation,
    map_type,
    mapping_table_markdown,
)
from tabfree_migrate.tde import (
    LEGACY_HYPER_API_VERSION,
    copy_table_sql,
    list_tables_sql,
    tde_reader_available,
    upgrade_advice,
    upgrade_tde,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "packages/migrate/README.md"
EXTRACTS = REPO_ROOT / "corpus/extracts"


@pytest.mark.parametrize(
    ("hyper", "precision", "scale", "athena", "as_text", "parity"),
    [
        ("BOOL", None, None, "boolean", False, ParityKind.BOOLEAN),
        ("SMALL_INT", None, None, "integer", False, ParityKind.INTEGER),
        ("BIG_INT", None, None, "bigint", False, ParityKind.INTEGER),
        ("OID", None, None, "bigint", False, ParityKind.INTEGER),
        ("NUMERIC", 18, 2, "decimal(18,2)", False, ParityKind.DECIMAL),
        ("NUMERIC", 38, 0, "decimal(38,0)", False, ParityKind.DECIMAL),
        ("NUMERIC", None, None, "double", False, ParityKind.FLOAT),
        ("DOUBLE", None, None, "double", False, ParityKind.FLOAT),
        ("FLOAT", None, None, "float", False, ParityKind.FLOAT32),
        ("TEXT", None, None, "string", False, ParityKind.TEXT),
        ("JSON", None, None, "string", False, ParityKind.TEXT),
        ("DATE", None, None, "date", False, ParityKind.DATE),
        ("TIMESTAMP", None, None, "timestamp", False, ParityKind.TIMESTAMP),
        ("TIMESTAMP_TZ", None, None, "timestamp", False, ParityKind.TIMESTAMP_TZ),
        ("TIME", None, None, "string", True, ParityKind.TEXT),
        ("INTERVAL", None, None, "string", True, ParityKind.TEXT),
        ("GEOGRAPHY", None, None, "string", True, ParityKind.WKT),
        ("BYTES", None, None, "binary", False, ParityKind.BINARY),
        ("TypeTag.BIG_INT", None, None, "bigint", False, ParityKind.INTEGER),
        ("SOMETHING_NEW", None, None, "string", True, ParityKind.TEXT),
    ],
)
def test_map_type(
    hyper: str, precision: int | None, scale: int | None, athena: str, as_text: bool, parity: ParityKind
) -> None:
    mapped = map_type(hyper, precision, scale)
    assert (mapped.athena, mapped.export_as_text, mapped.parity) == (athena, as_text, parity)


def test_lossy_and_unknown_types_are_noted() -> None:
    assert any("double" in n for n in map_type("NUMERIC", 40, 2).notes)
    assert any("Unknown Hyper type" in n for n in map_type("SOMETHING_NEW").notes)
    assert any(str(GEOGRAPHY_SRID) in n and "WKT" in n for n in map_type("GEOGRAPHY").notes)
    assert any("UTC" in n for n in map_type("TIMESTAMP_TZ").notes)


def test_collation_notes() -> None:
    assert is_binary_collation(None)
    assert is_binary_collation("BINARY")
    assert not is_binary_collation("en_US_CI")
    assert not any("collation" in n for n in map_type("TEXT", collation="binary").notes[1:])
    assert any("en_US_CI" in n for n in map_type("TEXT", collation="en_US_CI").notes)


def test_readme_documents_the_mapping_table() -> None:
    table = mapping_table_markdown()
    assert all(f"| `{name}` |" in table for name in TYPE_RULES)
    assert table in README.read_text(encoding="utf-8"), (
        "regenerate the README table from mapping_table_markdown()"
    )


def test_detect_format_from_magic_bytes() -> None:
    assert detect_format(b"Hyper\x08\x00\x00") is ExtractFormat.HYPER
    assert detect_format(b" \x02\x01b\xa0\x1e\xab\x07\x02\x00") is ExtractFormat.TDE
    assert detect_format(b"PK\x03\x04rest") is ExtractFormat.PACKAGE
    assert detect_format(b"<?xml") is ExtractFormat.UNKNOWN
    assert detect_format(b"") is ExtractFormat.UNKNOWN


@pytest.mark.skipif(not EXTRACTS.exists(), reason="corpus not present")
def test_every_corpus_extract_is_recognised_by_content() -> None:
    files = [p for p in EXTRACTS.rglob("*") if p.suffix in {".hyper", ".tde"}]
    assert files
    for path in files:
        assert sniff(path).value == path.suffix[1:], path


def test_tde_sql_matches_the_legacy_sample() -> None:
    listing = list_tables_sql("/data/it's.tde")
    assert "external('/data/it''s.tde', format => 'tde', \"table\" => 'SYS.TABLES')" in listing
    assert "\"TABLES_NAME\" <> '$TableauMetadata'" in listing
    copy = copy_table_sql("/x.tde", "Extract", 'Ex"tract')
    assert copy.startswith('CREATE TABLE "Extract"."Ex""tract" AS SELECT * FROM external(')
    assert '"table" => \'Extract.Ex"tract\'' in copy


def test_tde_upgrade_is_refused_without_the_pinned_api(tmp_path: Path) -> None:
    assert not tde_reader_available()  # dev and CI environments never install 0.0.17537
    with pytest.raises(RuntimeError, match=LEGACY_HYPER_API_VERSION):
        upgrade_tde(tmp_path / "a.tde", tmp_path / "a.hyper")
    advice = upgrade_advice()
    assert advice["status"] == "needs-upgrade"
    assert any("Dockerfile.tde-legacy" in o for o in advice["options"])
