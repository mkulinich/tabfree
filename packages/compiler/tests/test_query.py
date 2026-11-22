"""End-to-end query compilation on DuckDB with the example Superstore model and dashboard."""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import pytest
import sqlglot

from tabfree_compiler import CompileError, SemanticModel, Session, UnsupportedError, compile_query
from tabfree_compiler.sql import DIALECTS
from tabfree_schemas import load_document

duckdb = pytest.importorskip("duckdb")

EXAMPLES = Path(__file__).resolve().parents[2] / "schemas" / "examples"
MODEL_DOC = load_document(EXAMPLES / "models" / "superstore_sales.model.yaml")
MANIFEST = json.loads(
    (EXAMPLES / "dashboards" / "sales-overview" / "tabfree.json").read_text(encoding="utf-8")
)
EXEC = Session(username="erin", groups=frozenset({"Executives"}), today=dt.date(2024, 4, 15))

# row_id, order_date, customer, segment, region, state, category, sales, quantity, discount, profit
ORDERS: list[tuple[Any, ...]] = [
    (1, "2024-01-15", "Ann", "Consumer", "East", "NY", "Furniture", 100.0, 1, 0.0, 10.0),
    (2, "2024-01-20", "Ann", "Consumer", "East", "NY", "Technology", 200.0, 2, 0.1, 50.0),
    (3, "2024-02-05", "Bob", "Corporate", "West", "CA", "Furniture", 300.0, 3, 0.2, -20.0),
    (4, "2024-02-10", "Cal", "Consumer", "West", "WA", "Office Supplies", 50.0, 1, 0.0, 5.0),
    (5, "2024-03-01", "Dee", "Home Office", "Central", "TX", "Technology", 400.0, 4, 0.0, 100.0),
    (6, "2024-03-15", "Ann", "Consumer", "East", "MA", "Office Supplies", 25.0, 1, 0.0, 2.0),
    (7, "2024-03-20", "Eve", "Corporate", "South", "FL", "Furniture", 80.0, 1, 0.3, 8.0),
    (8, "2024-04-02", "Bob", "Corporate", "West", "CA", "Technology", 120.0, 2, 0.0, 30.0),
    (9, "2024-04-03", "Dee", "Home Office", None, None, "Office Supplies", 60.0, 1, 0.0, 6.0),
    (10, "2024-04-10", "Eve", "Corporate", "Central", "TX", "Furniture", 90.0, 1, 0.0, -9.0),
]
PEOPLE = [("East", "alice"), ("West", "bob"), ("Central", "carol")]
MANAGER = dict(PEOPLE)


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute("CREATE SCHEMA tabfree_lake")
    db.execute(
        "CREATE TABLE tabfree_lake.orders (row_id INTEGER, order_id VARCHAR, order_date DATE, "
        "ship_mode VARCHAR, customer_name VARCHAR, segment VARCHAR, region VARCHAR, state VARCHAR, "
        "category VARCHAR, sub_category VARCHAR, sales DOUBLE, quantity INTEGER, discount DOUBLE, "
        "profit DOUBLE)"
    )
    for r in ORDERS:
        db.execute(
            "INSERT INTO tabfree_lake.orders VALUES (?, ?, ?, 'Standard', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [r[0], f"O-{r[0]}", r[1], r[2], r[3], r[4], r[5], r[6], r[6], r[7], r[8], r[9], r[10]],
        )
    db.execute("CREATE TABLE tabfree_lake.people (region VARCHAR, regional_manager VARCHAR)")
    db.executemany("INSERT INTO tabfree_lake.people VALUES (?, ?)", PEOPLE)
    return db


@pytest.fixture(scope="module")
def model() -> SemanticModel:
    return SemanticModel.from_dict(MODEL_DOC)


def run(
    con: Any, model: SemanticModel, query: dict[str, Any], session: Session = EXEC, **kw: Any
) -> list[dict[str, Any]]:
    compiled = compile_query(model, query, dialect="duckdb", session=session, **kw)
    cursor = con.execute(compiled.sql, list(compiled.parameters))
    names = [c.name for c in compiled.columns]
    assert [d[0] for d in cursor.description] == names
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def by(rows: list[dict[str, Any]], key: str) -> dict[Any, dict[str, Any]]:
    return {r[key]: r for r in rows}


def totals(key: int, value: int, rows: list[tuple[Any, ...]] = ORDERS) -> dict[Any, float]:
    out: dict[Any, float] = defaultdict(float)
    for r in rows:
        out[r[key]] += r[value]
    return dict(out)


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
def test_example_dashboard_compiles_in_every_dialect(model: SemanticModel, dialect: str) -> None:
    for name, query in MANIFEST["queries"].items():
        compiled = compile_query(model, query, dialect=dialect, session=EXEC)  # type: ignore[arg-type]
        read = "trino" if dialect == "athena" else dialect
        sql = compiled.sql if dialect == "redshift" else compiled.sql.replace("?", "NULL")
        assert sqlglot.parse_one(sql, read=read) is not None, name


def test_sales_and_profit_ratio_by_region(con: Any, model: SemanticModel) -> None:
    rows = by(run(con, model, MANIFEST["queries"]["by_region"]), "Region")
    sales = totals(4, 7)
    profit = totals(4, 10)
    assert set(rows) == set(sales)
    for region, total in sales.items():
        assert rows[region]["Sales"] == pytest.approx(total)
        assert rows[region]["Profit Ratio"] == pytest.approx(profit[region] / total)
    ordered = [r["Region"] for r in run(con, model, MANIFEST["queries"]["by_region"])]
    assert ordered == sorted(sales, key=lambda k: -sales[k])  # sort: Sales desc


def test_kpis_without_dimensions(con: Any, model: SemanticModel) -> None:
    [row] = run(con, model, MANIFEST["queries"]["kpis"])
    assert row["Sales"] == pytest.approx(sum(r[7] for r in ORDERS))
    assert row["Profit Ratio"] == pytest.approx(sum(r[10] for r in ORDERS) / sum(r[7] for r in ORDERS))


def test_monthly_trend(con: Any, model: SemanticModel) -> None:
    rows = run(con, model, MANIFEST["queries"]["trend"])
    months = [r["Month"] for r in rows]
    assert months == [dt.datetime(2024, m, 1) for m in (1, 2, 3, 4)]
    assert rows[0]["Sales"] == pytest.approx(300.0)


def test_rls_limits_a_manager_to_their_region(con: Any, model: SemanticModel) -> None:
    alice = Session(username="alice")
    compiled = compile_query(model, MANIFEST["queries"]["by_region"], dialect="duckdb", session=alice)
    assert "alice" not in compiled.sql
    assert "alice" in compiled.parameters
    rows = run(con, model, MANIFEST["queries"]["by_region"], session=alice)
    assert [r["Region"] for r in rows] == ["East"]
    assert rows[0]["Sales"] == pytest.approx(325.0)


def test_rls_is_not_injectable(con: Any, model: SemanticModel) -> None:
    evil = Session(username="' OR '1'='1")
    assert run(con, model, MANIFEST["queries"]["kpis"], session=evil) == [
        {"Sales": None, "Profit": None, "Profit Ratio": None}
    ]


def test_rls_exempt_group_sees_everything(con: Any, model: SemanticModel) -> None:
    [row] = run(con, model, {"measures": ["Sales"]})
    assert row["Sales"] == pytest.approx(1425.0)


def test_join_through_many_to_one_relationship(con: Any, model: SemanticModel) -> None:
    rows = by(
        run(con, model, {"dimensions": ["Regional Manager"], "measures": ["Sales"]}), "Regional Manager"
    )
    expected: dict[Any, float] = defaultdict(float)
    for r in ORDERS:
        expected[MANAGER.get(r[4])] += r[7]
    assert {k: v["Sales"] for k, v in rows.items()} == pytest.approx(dict(expected))


def test_categorical_filters(con: Any, model: SemanticModel) -> None:
    q = {"dimensions": ["Region"], "measures": ["Sales"]}
    keep = run(
        con, model, q, filters=[{"field": "Region", "kind": "categorical", "members": ["East", "West"]}]
    )
    assert {r["Region"] for r in keep} == {"East", "West"}
    excluded = run(
        con,
        model,
        q,
        filters=[{"field": "Region", "kind": "categorical", "members": ["East"], "exclude": True}],
    )
    assert {r["Region"] for r in excluded} == {"West", "Central", "South", None}  # NULLs survive exclude
    with_null = run(con, model, q, filters=[{"field": "Region", "kind": "categorical", "members": [None]}])
    assert [r["Region"] for r in with_null] == [None]


def test_relative_date_filter(con: Any, model: SemanticModel) -> None:
    flt = {
        "field": "Order Date",
        "kind": "relative_date",
        "relative": {"period": "month", "from": -1, "to": 0},
    }
    [row] = run(con, model, {"measures": ["Sales"]}, filters=[flt])
    march_april = sum(r[7] for r in ORDERS if r[1] >= "2024-03-01")
    assert row["Sales"] == pytest.approx(march_april)
    anchored = {**flt, "relative": {"period": "month", "from": 0, "to": 0, "anchor": "2024-01-31"}}
    [row] = run(con, model, {"measures": ["Sales"]}, filters=[anchored])
    assert row["Sales"] == pytest.approx(300.0)


def test_range_filter_on_a_dimension_and_on_a_measure(con: Any, model: SemanticModel) -> None:
    q = {"dimensions": ["Region"], "measures": ["Sales"]}
    big = run(con, model, q, filters=[{"field": "Sales", "kind": "range", "min": 400}])
    assert {r["Region"] for r in big} == {k for k, v in totals(4, 7).items() if v >= 400}
    dated = run(
        con,
        model,
        {"measures": ["Sales"]},
        filters=[{"field": "Order Date", "kind": "range", "max": "2024-01-31"}],
    )
    assert dated[0]["Sales"] == pytest.approx(300.0)


def test_top_n_uses_the_parameter(con: Any, model: SemanticModel) -> None:
    q = MANIFEST["queries"]["top_customers"]
    customers = totals(2, 7)
    ranked = sorted(customers, key=lambda c: -customers[c])
    rows = run(con, model, q, parameters={"top_n": 2})
    assert [r["Customer Name"] for r in rows] == ranked[:2]
    assert len(run(con, model, q)) == len(customers)  # default 10 covers every customer


def test_fixed_lod_ignores_dimension_filters_but_not_context_filters(con: Any, model: SemanticModel) -> None:
    q = {"measures": ["Customer Lifetime Sales"]}
    furniture = {"field": "Category", "kind": "categorical", "members": ["Furniture"]}
    furniture_buyers = {r[2] for r in ORDERS if r[6] == "Furniture"}
    lifetime = totals(2, 7)
    [row] = run(con, model, q, filters=[furniture])
    assert row["Customer Lifetime Sales"] == pytest.approx(mean(lifetime[c] for c in furniture_buyers))
    [row] = run(con, model, q, filters=[{**furniture, "context": True}])
    furniture_only = totals(2, 7, [r for r in ORDERS if r[6] == "Furniture"])
    assert row["Customer Lifetime Sales"] == pytest.approx(mean(furniture_only.values()))


def test_fixed_lod_coarser_than_the_view_replicates(con: Any, model: SemanticModel) -> None:
    q = {
        "dimensions": ["Region", "Category"],
        "measures": [{"expr": "SUM({FIXED [Region] : SUM([Sales])})", "as": "Region Sales"}],
    }
    rows = run(con, model, q)
    region_sales = totals(4, 7)
    for r in rows:
        assert r["Region Sales"] == pytest.approx(region_sales[r["Region"]])


def test_include_lod_averages_per_customer(con: Any, model: SemanticModel) -> None:
    q = {
        "dimensions": ["Region"],
        "measures": [{"expr": "AVG({INCLUDE [Customer Name] : SUM([Sales])})", "as": "Avg"}],
    }
    rows = by(run(con, model, q), "Region")
    per: dict[Any, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in ORDERS:
        per[r[4]][r[2]] += r[7]
    for region, customers in per.items():
        assert rows[region]["Avg"] == pytest.approx(mean(customers.values()))


def test_exclude_lod_shows_the_total_without_the_excluded_dimension(con: Any, model: SemanticModel) -> None:
    q = {
        "dimensions": ["Region", "Category"],
        "measures": [{"expr": "SUM({EXCLUDE [Category] : SUM([Sales])})", "as": "Region Total"}],
    }
    region_sales = totals(4, 7)
    for r in run(con, model, q):
        assert r["Region Total"] == pytest.approx(region_sales[r["Region"]])


def test_row_level_lod_in_a_calculation(con: Any, model: SemanticModel) -> None:
    q = {"measures": [{"expr": "SUM([Sales] / {FIXED [Region] : SUM([Sales])})", "as": "Share Sum"}]}
    [row] = run(con, model, q)
    assert row["Share Sum"] == pytest.approx(len(totals(4, 7)))  # each region's shares sum to 1


def test_date_parts_sort_and_limit(con: Any, model: SemanticModel) -> None:
    q = {
        "dimensions": [{"field": "Order Date", "datePart": "month", "as": "Month"}],
        "measures": ["Quantity"],
        "sort": [{"by": "Quantity", "dir": "desc"}],
        "limit": 2,
    }
    rows = run(con, model, q)
    assert len(rows) == 2
    assert rows[0]["Quantity"] >= rows[1]["Quantity"]
    assert {r["Month"] for r in rows} <= {1, 2, 3, 4}


def test_dimension_only_query_returns_distinct_members(con: Any, model: SemanticModel) -> None:
    rows = run(con, model, {"dimensions": ["Segment"]})
    assert sorted(r["Segment"] for r in rows) == sorted({r[3] for r in ORDERS})


def test_wildcard_and_condition_filters(con: Any, model: SemanticModel) -> None:
    q = {"dimensions": ["Customer Name"], "measures": ["Sales"]}
    rows = run(
        con,
        model,
        q,
        filters=[
            {"field": "Customer Name", "kind": "wildcard", "wildcard": {"match": "starts_with", "value": "a"}}
        ],
    )
    assert [r["Customer Name"] for r in rows] == ["Ann"]
    rows = run(
        con,
        model,
        q,
        filters=[{"field": "Customer Name", "kind": "condition", "condition": "SUM([Profit]) < 0"}],
    )
    assert {r["Customer Name"] for r in rows} == {c for c, p in totals(2, 10).items() if p < 0}


def test_redshift_uses_named_parameters(model: SemanticModel) -> None:
    compiled = compile_query(
        model, MANIFEST["queries"]["by_region"], dialect="redshift", session=Session(username="bob")
    )
    assert compiled.parameters == ()
    assert list(compiled.named_parameters.values()) == ["bob"]
    name = next(iter(compiled.named_parameters))
    assert f":{name}" in compiled.sql


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ({"dimensions": ["Nope"]}, "unknown field"),
        ({"dimensions": [{"expr": "SUM([Sales])", "as": "x"}]}, "aggregate"),
        ({"measures": [{"expr": "RUNNING_SUM(SUM([Sales]))", "as": "x"}]}, "table calculation"),
        ({"measures": [{"expr": "SUM([Sales]) + [Profit]", "as": "x"}]}, "must be aggregated"),
        ({"dimensions": ["Region", "Region"]}, "unique"),
        ({"dimensions": ["Region"], "sort": [{"by": "Sales"}]}, "sort by unknown output"),
    ],
)
def test_compile_errors(model: SemanticModel, query: dict[str, Any], error: str) -> None:
    with pytest.raises(CompileError, match=error):
        compile_query(model, query, session=EXEC)


def test_self_referencing_calc_is_rejected() -> None:
    doc = {
        **MODEL_DOC,
        "fields": [
            *MODEL_DOC["fields"],
            {"id": "loop", "name": "Loop", "role": "measure", "type": "float", "formula": "[Loop] + 1"},
        ],
    }
    with pytest.raises(CompileError, match="refers to itself"):
        compile_query(doc, {"measures": ["Loop"]}, session=EXEC)


def test_unsafe_identifiers_are_rejected() -> None:
    tables = [
        {**t, "source": {"table": 'orders"; DROP TABLE x; --'}} if t["id"] == "orders" else t
        for t in MODEL_DOC["tables"]
    ]
    with pytest.raises(CompileError, match="invalid table identifier"):
        compile_query({**MODEL_DOC, "tables": tables}, {"measures": ["Sales"]}, session=EXEC)


def test_unlinked_tables_are_unsupported_for_now() -> None:
    with pytest.raises(UnsupportedError, match="many-to-one"):
        compile_query(
            {**MODEL_DOC, "relationships": []},
            {"dimensions": ["Regional Manager"], "measures": ["Sales"]},
            session=EXEC,
        )


def test_data_source_filters_apply_before_everything(con: Any) -> None:
    doc = {**MODEL_DOC, "dataSourceFilters": [{"formula": "[Region] <> 'South'"}]}
    [row] = run(con, SemanticModel.from_dict(doc), {"measures": ["Customer Lifetime Sales"]})
    # NULL <> 'South' is NULL, so rows without a region are dropped too, as in Tableau.
    lifetime = totals(2, 7, [r for r in ORDERS if r[4] is not None and r[4] != "South"])
    assert row["Customer Lifetime Sales"] == pytest.approx(mean(lifetime.values()))


def test_filter_values_with_quotes_are_escaped(con: Any, model: SemanticModel) -> None:
    flt = {"field": "Customer Name", "kind": "categorical", "members": ["O'Brien'; DROP TABLE x; --"]}
    [row] = run(con, model, {"measures": ["Sales"]}, filters=[flt])
    assert row["Sales"] is None


def test_null_relationship_keys_do_not_match(con: Any) -> None:
    con.execute("INSERT INTO tabfree_lake.people VALUES (NULL, 'nobody')")
    try:
        model = SemanticModel.from_dict(MODEL_DOC)
        rows = by(
            run(con, model, {"dimensions": ["Regional Manager"], "measures": ["Sales"]}), "Regional Manager"
        )
        assert "nobody" not in rows
    finally:
        con.execute("DELETE FROM tabfree_lake.people WHERE region IS NULL")


def test_cli_compiles_a_query_and_runs_tdvt(capsys: pytest.CaptureFixture[str]) -> None:
    from tabfree_compiler.cli import main

    model = EXAMPLES / "models" / "superstore_sales.model.yaml"
    manifest = EXAMPLES / "dashboards" / "sales-overview" / "tabfree.json"
    assert (
        main(["query", str(model), str(manifest), "top_customers", "--user", "alice", "--param", "top_n=3"])
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["parameters"] == ["alice"]
    assert out["columns"][0]["name"] == "Customer Name"
    assert main(["query", str(model), str(manifest), "missing"]) == 2
    assert main(["expr", str(model), "SUM([Sales]) / ", "--dialect", "redshift"]) == 1
