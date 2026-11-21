from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabfree_schemas.bundle import check_bundle, check_bundle_dir
from tabfree_schemas.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "dashboards"
MANIFEST: dict[str, Any] = json.loads(
    (EXAMPLES / "sales-overview" / "tabfree.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "bundle", sorted(p.name for p in EXAMPLES.iterdir() if (p / "tabfree.json").exists())
)
def test_example_bundles_are_consistent(bundle: str) -> None:
    report = check_bundle_dir(EXAMPLES / bundle)
    assert report.errors == []
    assert report.components > 0
    assert not report.custom_js


@pytest.mark.parametrize(
    ("html", "error"),
    [
        ('<tf-chart query="nope" x="a" y="b"></tf-chart>', 'query="nope" is not a query'),
        ('<tf-chart query="by_region" x="Profit" y="Region"></tf-chart>', 'x="Profit" is not an output'),
        ('<tf-chart query="by_region" mark="pie" x="Sales" y="Region"></tf-chart>', "mark='pie'"),
        ('<tf-chart query="by_region" x="Sales"></tf-chart>', 'missing the "y" attribute'),
        ('<tf-filter name="country"></tf-filter>', 'name="country" is not a filter'),
        ('<tf-parameter name="region"></tf-parameter>', 'name="region" is not a parameter'),
        ("<tf-sparkle></tf-sparkle>", "unknown component"),
        ('<tf-kpi query="kpis" measure="Sales" change="Growth"></tf-kpi>', 'change="Growth"'),
        ('<script src="https://cdn.example/x.js"></script>', "<script src>"),
        ('<img src="https://tracker.example/p.gif">', "remote URL"),
    ],
)
def test_bad_references_are_errors(html: str, error: str) -> None:
    report = check_bundle(MANIFEST, html)
    assert not report.ok
    assert any(error in e for e in report.errors), report.errors


def test_table_calc_outputs_are_valid_columns() -> None:
    assert check_bundle(
        MANIFEST, '<tf-chart query="trend" mark="line" x="Month" y="Running Sales"></tf-chart>'
    ).ok


def test_custom_js_is_flagged_with_risky_apis() -> None:
    report = check_bundle(
        MANIFEST, "<script>const r = await tabfree.query('kpis'); fetch('//x?' + r)</script>"
    )
    assert report.ok
    assert report.custom_js
    assert "network" in report.warnings[0]
    quiet = check_bundle(MANIFEST, "<script>const r = await tabfree.query('kpis');</script>")
    assert quiet.warnings == ["index.html: custom JavaScript needs a human review before publishing"]


def test_invalid_manifest_stops_early() -> None:
    report = check_bundle({"schema": "tabfree/dashboard@0"}, "")
    assert report.errors
    assert report.errors[0].startswith("tabfree.json:")


def test_cli_checks_bundle_directories(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(EXAMPLES / "sales-overview")]) == 0
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "tabfree.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (bad / "index.html").write_text('<tf-kpi query="kpis" measure="Nope"></tf-kpi>', encoding="utf-8")
    assert main([str(bad)]) == 1
    assert 'measure="Nope"' in capsys.readouterr().out


def test_controls_and_action_fields_are_checked() -> None:
    report = check_bundle(MANIFEST, '<tf-filter name="region" control="chekclist"></tf-filter>')
    assert any("control='chekclist'" in e for e in report.errors)
    bad_action = {
        **MANIFEST,
        "actions": [{**MANIFEST["actions"][0], "fields": [{"source": "Grp", "target": "Region"}]}],
    }
    report = check_bundle(bad_action, "")
    assert any("'Grp' is not an output of the source query" in e for e in report.errors)
