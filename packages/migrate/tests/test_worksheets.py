"""P2.5: worksheet IR → TabFree query (grain from shelves and encodings, filters, sorts, totals)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.modelgen import GenerationReport, generate_model
from tabfree_migrate.twb import parse_file
from tabfree_migrate.worksheets import worksheet_query

REPO = Path(__file__).resolve().parents[3]
DIMENSIONAL = next(
    REPO.glob("corpus/workbooks/**/Dimensional-Flat example Hierarchy Navigator_v2018.2.twbx"), None
)
SUPERSTORE = REPO / "corpus/derived/superstore/orders.parquet"
DS = "federated.abc"


def ref(name: str, derivation: str = "none", kind: str = "nk", ds: str = DS) -> dict[str, Any]:
    return {
        "ref": f"[{ds}].[{derivation}:{name}:{kind}]",
        "datasource": ds,
        "kind": kind,
        "derivation": derivation,
        "field": f"[{name}]",
    }


def shelf(*refs: dict[str, Any]) -> dict[str, Any]:
    nodes = [{"op": "field", "ref": r} for r in refs]
    return {"tree": nodes[0] if len(nodes) == 1 else {"op": "nest", "args": nodes}}


MODEL = GenerationReport(
    names={
        "[Region]": "Region",
        "[Order Date]": "Order Date",
        "[Sales]": "Sales",
        "[Calculation_1]": "Profit Ratio",
        "[Customer]": "Customer Name",
        "[Category]": "Category",
    },
    params={"[Parameter 1]": "Top N"},
)


def ir(**sheet: Any) -> dict[str, Any]:
    return {
        "datasources": [{"name": DS, "caption": "Orders"}],
        "worksheets": [{"name": "S", "datasources": [DS], **sheet}],
    }


def test_shelves_and_encodings_set_the_grain() -> None:
    query, report = worksheet_query(
        ir(
            rows=shelf(ref("Region"), ref("Order Date", "yr", "ok")),
            cols=shelf(ref("Sales", "sum", "qk"), ref("Order Date", "tmn", "qk")),
            panes=[
                {
                    "encodings": {
                        "color": [ref("Category")],
                        "text": [ref("Calculation_1", "usr", "qk")],
                        "tooltip": [ref("Customer", "ctd", "qk"), ref("Region")],
                    }
                }
            ],
        ),
        "S",
        MODEL,
    )
    assert report.ok, report.unsupported
    assert query["dimensions"] == [
        "Region",
        {"field": "Order Date", "datePart": "year", "as": "YEAR(Order Date)"},
        {"field": "Order Date", "dateTrunc": "month", "as": "MONTH_TRUNC(Order Date)"},
        "Category",
    ]
    assert query["measures"] == [
        {"field": "Sales", "agg": "sum", "as": "SUM(Sales)"},
        "Profit Ratio",
        {"field": "Customer Name", "agg": "countd", "as": "COUNTD(Customer Name)"},
    ]


def test_filters() -> None:
    filters = [
        {
            **ref("Region"),
            "kind": "categorical",
            "ref": ref("Region"),
            "members": ['"East"', '"West"'],
            "tree": {"ui": {"ui-enumeration": "exclusive"}},
            "context": True,
        },
        {"kind": "categorical", "ref": ref("Category"), "all": True},
        {"kind": "quantitative", "ref": ref("Sales", "sum", "qk"), "min": "10.5", "max": "100"},
        {"kind": "quantitative", "ref": ref("Order Date", "none", "qk"), "min": "#2024-01-01#"},
        {
            "kind": "relative-date",
            "ref": ref("Order Date", "none", "qk"),
            "period": "month",
            "firstPeriod": -11,
            "lastPeriod": 0,
        },
        {
            "kind": "top-n",
            "ref": ref("Customer"),
            "topN": {"end": "top", "count": "[Parameters].[Parameter 1]", "by": "SUM([Sales])"},
        },
        {"kind": "condition", "ref": ref("Customer"), "condition": "SUM([Sales]) > 100"},
        {"kind": "condition", "ref": ref("Region"), "condition": "NOT ISNULL([Region])"},
        {
            "kind": "wildcard",
            "ref": ref("Customer"),
            "wildcard": {"ui-pattern_text": "an", "ui-pattern_type": "startsWith"},
        },
        {"kind": "categorical", "ref": ref("Order Date", "yr", "ok"), "members": ["2024"]},
    ]
    query, report = worksheet_query(ir(rows=shelf(ref("Region")), filters=filters), "S", MODEL)
    assert report.ok, report.unsupported
    assert query["where"] == [
        {
            "field": "Region",
            "kind": "categorical",
            "members": ["East", "West"],
            "exclude": True,
            "context": True,
        },
        {"expr": "SUM([Sales])", "kind": "range", "min": 10.5, "max": 100},
        {"field": "Order Date", "kind": "range", "min": "2024-01-01"},
        {
            "field": "Order Date",
            "kind": "relative_date",
            "relative": {"period": "month", "from": -11, "to": 0},
        },
        {
            "field": "Customer Name",
            "kind": "top_n",
            "topN": {"n": {"param": "Top N"}, "by": "SUM([Sales])", "direction": "top"},
        },
        {"field": "Customer Name", "kind": "condition", "condition": "SUM([Sales]) > 100"},
        {"expr": "NOT ISNULL([Region])", "kind": "categorical", "members": [True]},
        {"field": "Customer Name", "kind": "wildcard", "wildcard": {"match": "starts_with", "value": "an"}},
        {"expr": "YEAR([Order Date])", "kind": "categorical", "members": [2024]},
    ]


def test_sorts_totals_and_what_is_not_converted() -> None:
    query, report = worksheet_query(
        ir(
            rows=shelf(ref("Region"), ref("Category"), ref("Sales", "pcto", "qk")),
            cols=shelf(ref("Sales", "sum", "qk", ds="federated.other")),
            sorts=[
                {
                    "kind": "computed",
                    "field": f"[{DS}].[none:Region:nk]",
                    "direction": "DESC",
                    "using": f"[{DS}].[sum:Sales:qk]",
                },
                {"kind": "manual", "field": f"[{DS}].[none:Category:nk]", "order": ['"B"', '"A"', "%all%"]},
            ],
            totals={"rows": True},
        ),
        "S",
        MODEL,
    )
    assert query["sort"] == [
        {"by": "Region", "dir": "desc", "expr": "SUM([Sales])"},
        {"by": "Category", "order": ["B", "A"]},
    ]
    assert query["totals"] == {"grand": True}
    assert not report.ok
    assert any("pcto" in u for u in report.unsupported)  # quick table calc
    assert any("federated.other" in u for u in report.unsupported)  # blended field


def test_measure_names_take_the_measures_from_their_filter() -> None:
    query, report = worksheet_query(
        ir(
            rows=shelf(ref("Region"), {"field": "[:Measure Names]", "datasource": DS}),
            filters=[
                {
                    "kind": "categorical",
                    "ref": {"field": "[:Measure Names]", "datasource": DS},
                    "members": [f'"[{DS}].[sum:Sales:qk]"', f'"[{DS}].[usr:Calculation_1:qk]"'],
                },
            ],
        ),
        "S",
        MODEL,
    )
    assert report.ok
    assert query["measures"] == [{"field": "Sales", "agg": "sum", "as": "SUM(Sales)"}, "Profit Ratio"]
    assert "where" not in query


def test_unknown_worksheet() -> None:
    with pytest.raises(ValueError, match="no worksheet"):
        worksheet_query(ir(), "Nope", MODEL)


@pytest.mark.skipif(DIMENSIONAL is None or not SUPERSTORE.is_file(), reason="corpus or data absent")
def test_superstore_worksheet_runs_at_the_tableau_grain() -> None:
    duckdb = pytest.importorskip("duckdb")
    from tabfree_compiler import compile_query

    assert DIMENSIONAL is not None
    workbook = parse_file(DIMENSIONAL)
    doc, model = generate_model(workbook, model_id="superstore_generated", database="main")
    query, report = worksheet_query(workbook, "Ship cost per Segment", model)
    assert report.ok, report.unsupported
    assert query["dimensions"] == ["Segment", "Ship Mode"]
    assert query["measures"] == [{"field": "Discount", "agg": "avg", "as": "AVG(Discount)"}]
    assert query["where"] == [{"field": "Dynamic Filter", "kind": "categorical", "members": [True]}]

    [table] = doc["tables"]
    con = duckdb.connect()
    renames = {"country_region": "country"}
    cols = ", ".join(
        f'"{renames.get(c["name"].lower(), c["name"].lower())}" AS "{c["name"]}"' for c in table["columns"]
    )
    con.execute(f"CREATE VIEW main.orders AS SELECT {cols} FROM read_parquet('{SUPERSTORE}')")  # noqa: S608

    def run(q: dict[str, Any]) -> dict[tuple[Any, ...], float]:
        compiled = compile_query(doc, q, dialect="duckdb")
        return {tuple(r[:-1]): r[-1] for r in con.execute(compiled.sql, list(compiled.parameters)).fetchall()}

    # The grain and the measure, checked against plain SQL without the parameter-driven filter.
    unfiltered = run({k: v for k, v in query.items() if k != "where"})
    expected = {
        (s, m): v
        for s, m, v in con.execute(
            'SELECT "Segment", "Ship_Mode", AVG("Discount") FROM main.orders GROUP BY 1, 2'
        ).fetchall()
    }
    assert unfiltered.keys() == expected.keys()
    assert len(expected) == 12
    for key, value in expected.items():
        assert unfiltered[key] == pytest.approx(value)
    filtered = run(query)
    assert filtered
    assert set(filtered) <= set(expected)


def test_corpus_worksheets_convert() -> None:
    manifest = json.loads((REPO / "corpus/manifest.json").read_text(encoding="utf-8"))
    sheets = ok = 0
    for entry in manifest["files"]:
        if entry.get("kind") not in ("twb", "twbx") or entry.get("expect_invalid"):
            continue
        workbook = parse_file(REPO / entry["path"])
        models: dict[str, GenerationReport | None] = {}
        for sheet in workbook["worksheets"]:
            sources = [d for d in sheet.get("datasources", []) if d != "Parameters"]
            if not sources:
                continue
            if sources[0] not in models:
                try:
                    models[sources[0]] = generate_model(workbook, datasource=sources[0])[1]
                except ValueError:
                    models[sources[0]] = None
            model = models[sources[0]]
            if model is None:
                continue
            _, report = worksheet_query(workbook, sheet["name"], model)
            sheets += 1
            ok += report.ok
    assert sheets > 500
    assert ok / sheets > 0.85  # the rest: quick table calcs, blends, geocoded fields


@pytest.mark.skipif(DIMENSIONAL is None, reason="corpus not present")
def test_worksheet_query_command() -> None:
    from typer.testing import CliRunner

    from tabfree_migrate import cli

    result = CliRunner().invoke(cli.app, ["worksheet-query", str(DIMENSIONAL), "Ship cost per Segment"])
    assert result.exit_code == 0, result.output
    assert '"Ship Mode"' in result.output
    missing = CliRunner().invoke(cli.app, ["worksheet-query", str(DIMENSIONAL), "Nope"])
    assert missing.exit_code == 1
    assert "no worksheet" in missing.output
