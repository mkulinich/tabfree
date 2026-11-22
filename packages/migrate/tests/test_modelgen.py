"""P2.4: semantic models generated from Tableau data sources, end to end on real Superstore data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.modelgen import generate_model, snake, tableau_literal
from tabfree_migrate.twb import parse_file

REPO = Path(__file__).resolve().parents[3]
DIMENSIONAL = next(
    REPO.glob("corpus/workbooks/**/Dimensional-Flat example Hierarchy Navigator_v2018.2.twbx"), None
)
SUPERSTORE = REPO / "corpus/derived/superstore/orders.parquet"
pytestmark = pytest.mark.skipif(DIMENSIONAL is None, reason="corpus not present")


@pytest.fixture(scope="module")
def generated() -> tuple[dict[str, Any], Any]:
    assert DIMENSIONAL is not None
    return generate_model(parse_file(DIMENSIONAL), model_id="superstore_generated", database="main")


def test_structure_and_translations(generated: tuple[dict[str, Any], Any]) -> None:
    doc, report = generated
    assert report.ok, (report.schema_errors, report.invalid_calcs)
    assert [t["source"]["table"] for t in doc["tables"]] == ["orders"]
    fields = {f["name"]: f for f in doc["fields"]}
    assert fields["Sales"]["defaultAggregation"] == "sum"
    assert fields["Postal Code"]["format"] == "00000"
    assert fields["State"]["semanticRole"] == "state_province"
    # Internal names become captions; parameters keep their own captions.
    assert fields["Total Sales"]["formula"] == "{fixed: sum([Sales])}"
    assert (
        "[Parameters].[Tree Level]" in fields["Dim for current level"]["formula"]
        or "[Parameters]." in fields["Dim for current level"]["formula"]
    )
    assert "Calculation_" not in json.dumps(doc["fields"])
    groups = {g["name"]: g for g in doc.get("groups", [])}
    assert "Manufacturer" in groups
    assert groups["Manufacturer"]["field"] == "Product Name"
    assert {h["name"] for h in doc["hierarchies"]} == {"Location", "Product"}
    assert any(p["name"] == "Top Customers" for p in doc["parameters"])
    assert any(s["name"] == "Top Customers by Profit" and "top" in s for s in doc["sets"])


@pytest.mark.skipif(not SUPERSTORE.is_file(), reason="Superstore data absent")
def test_generated_model_reproduces_superstore_totals(generated: tuple[dict[str, Any], Any]) -> None:
    duckdb = pytest.importorskip("duckdb")
    from tabfree_compiler import compile_query

    doc, _ = generated
    [table] = doc["tables"]
    con = duckdb.connect()
    renames = {"country_region": "country"}
    cols = ", ".join(
        f'"{renames.get(c["name"].lower(), c["name"].lower())}" AS "{c["name"]}"' for c in table["columns"]
    )
    con.execute(f"CREATE VIEW main.orders AS SELECT {cols} FROM read_parquet('{SUPERSTORE}')")  # noqa: S608

    def run(query: dict[str, Any]) -> list[tuple[Any, ...]]:
        compiled = compile_query(doc, query, dialect="duckdb")
        return [tuple(r) for r in con.execute(compiled.sql, list(compiled.parameters)).fetchall()]

    [(total, fixed)] = run({"measures": ["Sales", "Total Sales"]})
    assert total == pytest.approx(2_297_200.86, abs=0.01)
    assert fixed == pytest.approx(total)
    by_category = dict(run({"dimensions": ["Category"], "measures": ["Profit Ratio"]}))
    assert by_category["Furniture"] == pytest.approx(0.0249, abs=1e-4)
    # The workbook's own group of Product Name ("Manufacturer"), checked against its members.
    canon = next(g["groups"]["Canon"] for g in doc["groups"] if g["name"] == "Manufacturer")
    [(expected,)] = con.execute(
        'SELECT SUM("Sales") FROM main.orders WHERE "Product_Name" IN (SELECT UNNEST(?))', [canon]
    ).fetchall()
    by_maker = dict(run({"dimensions": ["Manufacturer"], "measures": ["Sales"]}))
    assert by_maker["Canon"] == pytest.approx(expected)
    assert sum(by_maker.values()) == pytest.approx(total)
    top = run({"dimensions": ["Customer Name", "Top Customers by Profit"]})
    assert sum(1 for _, inside in top if inside) == 5  # Top N from the "Top Customers" parameter


def test_every_corpus_data_source_generates_a_valid_model() -> None:
    manifest = json.loads((REPO / "corpus/manifest.json").read_text(encoding="utf-8"))
    models = calcs = invalid = 0
    for entry in manifest["files"]:
        if entry.get("kind") not in ("twb", "twbx", "tds", "tdsx") or entry.get("expect_invalid"):
            continue
        ir = parse_file(REPO / entry["path"])
        for ds in (d for d in ir["datasources"] if d.get("kind") != "parameters"):
            try:
                doc, report = generate_model(ir, datasource=ds.get("name"))
            except ValueError:
                continue  # a published source referenced by a workbook: no columns to model
            models += 1
            assert len(report.schema_errors) <= 1
            calcs += sum(1 for f in doc["fields"] if "formula" in f)
            invalid += len(report.invalid_calcs)
    assert models > 150
    assert invalid / calcs < 0.05  # unresolved references mostly point at published sources


def test_literals_and_ids() -> None:
    assert tableau_literal('"East"') == "East"
    assert tableau_literal("#2024-01-01#") == "2024-01-01"
    assert tableau_literal("%null%") is None
    assert tableau_literal("10") == 10
    assert snake("Sub-Category (copy)") == "sub_category_copy"
    assert snake("3D") == "f_3d"


def test_generate_model_command(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from tabfree_migrate import cli
    from tabfree_schemas import SchemaKind, load_document, schema_errors

    assert DIMENSIONAL is not None
    out = tmp_path / "model.yaml"
    result = CliRunner().invoke(
        cli.app, ["generate-model", str(DIMENSIONAL), "--out", str(out), "--id", "superstore_gen"]
    )
    assert result.exit_code == 0, result.output
    assert "assumptions:" in result.output
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, load_document(out)) == []
