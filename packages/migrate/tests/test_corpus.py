from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tabfree_migrate import cli
from tabfree_migrate.corpus import verify_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]

WORKBOOK = (
    b"<?xml version='1.0' encoding='utf-8' ?><workbook source-build='2025.2.0' version='18.1'/>"
)
DATASOURCE = b"<?xml version='1.0' encoding='utf-8' ?><datasource version='18.1'/>"
BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<workbook>&lol2;</workbook>"""


def build_corpus(root: Path, files: dict[str, tuple[str, bytes]]) -> None:
    """Write files plus a manifest describing them (kind, sha256, bytes)."""
    entries = []
    for rel, (kind, data) in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append(
            {
                "path": rel,
                "kind": kind,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (root / "corpus").mkdir(exist_ok=True)
    (root / "corpus" / "manifest.json").write_text(json.dumps({"files": entries}), encoding="utf-8")


def zip_bytes(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
        archive.writestr("Data/Extracts/extract.hyper", b"not really hyper")
    return buffer.getvalue()


def test_valid_corpus_passes(tmp_path: Path) -> None:
    build_corpus(
        tmp_path,
        {
            "corpus/workbooks/a.twb": ("twb", WORKBOOK),
            "corpus/datasources/b.tdsx": ("tdsx", zip_bytes("b.tds", DATASOURCE)),
            "corpus/tdvt/expr.txt": ("tdvt-expr", b"SUM([num0])\n"),
        },
    )
    report = verify_corpus(tmp_path)
    assert report.problems == []
    assert report.checked == 3


def test_tampered_missing_and_wrong_root_are_reported(tmp_path: Path) -> None:
    build_corpus(
        tmp_path,
        {
            "corpus/workbooks/a.twb": ("twb", WORKBOOK),
            "corpus/workbooks/b.twb": ("twb", DATASOURCE),
            "corpus/workbooks/c.twb": ("twb", WORKBOOK),
        },
    )
    (tmp_path / "corpus/workbooks/a.twb").write_bytes(WORKBOOK + b" ")
    (tmp_path / "corpus/workbooks/c.twb").unlink()
    problems = verify_corpus(tmp_path).problems
    assert any("a.twb: sha256 does not match" in p for p in problems)
    assert any("b.twb: root element is <datasource>" in p for p in problems)
    assert any("c.twb: listed in the manifest but missing" in p for p in problems)


def test_entity_expansion_is_refused(tmp_path: Path) -> None:
    build_corpus(tmp_path, {"corpus/workbooks/evil.twb": ("twb", BILLION_LAUGHS)})
    problems = verify_corpus(tmp_path).problems
    assert len(problems) == 1
    assert "EntitiesForbidden" in problems[0]


def test_package_without_workbook_is_reported(tmp_path: Path) -> None:
    build_corpus(tmp_path, {"corpus/workbooks/empty.twbx": ("twbx", zip_bytes("notes.txt", b"hi"))})
    problems = verify_corpus(tmp_path).problems
    assert any("no .twb file inside the package" in p for p in problems)


def test_expect_invalid_fixtures(tmp_path: Path) -> None:
    build_corpus(
        tmp_path,
        {
            "corpus/workbooks/bad.twb": ("twb", b"<workbook><oops></workbook>"),
            "corpus/workbooks/good.twb": ("twb", WORKBOOK),
        },
    )
    manifest_path = tmp_path / "corpus" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        entry["expect_invalid"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_corpus(tmp_path).problems
    assert problems == ["corpus/workbooks/good.twb: marked expect_invalid but parses cleanly"]


def test_external_entries_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "corpus").mkdir()
    manifest = {
        "files": [{"kind": "tdvt-data", "url": "https://example.com/big.csv", "external": True}]
    }
    (tmp_path / "corpus" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = verify_corpus(tmp_path)
    assert (report.checked, report.skipped, report.problems) == (0, 1, [])


def test_corpus_check_command_exit_codes(tmp_path: Path) -> None:
    build_corpus(tmp_path, {"corpus/workbooks/a.twb": ("twb", WORKBOOK)})
    ok = CliRunner().invoke(cli.app, ["corpus-check", "--root", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    (tmp_path / "corpus/workbooks/a.twb").write_bytes(b"<workbook/>")
    bad = CliRunner().invoke(cli.app, ["corpus-check", "--root", str(tmp_path)])
    assert bad.exit_code == 1


@pytest.mark.skipif(
    not (REPO_ROOT / "corpus" / "manifest.json").exists(), reason="repository corpus not present"
)
def test_repository_corpus_matches_its_manifest() -> None:
    report = verify_corpus(REPO_ROOT)
    assert report.problems == []
    assert report.checked > 0
