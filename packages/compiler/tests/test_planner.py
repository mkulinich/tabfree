"""P2.5 planner features on DuckDB: groups, bins, sets, totals, Measure Names/Values, computed
sorts and native-grain aggregation over relationships."""

from __future__ import annotations

from typing import Any

import pytest

from tabfree_compiler import CompileError, UnsupportedError, compile_query

duckdb = pytest.importorskip("duckdb")

ORDERS = [  # id, region, customer, sales
    (1, "East", "Ann", 100.0),
    (2, "East", "Bob", 50.0),
    (3, "West", "Cal", 300.0),
    (4, "West", "Ann", 20.0),
    (5, "Central", "Dee", 10.0),
]
TARGETS = [("East", 500.0), ("West", 200.0), ("Central", 100.0)]

MODEL: dict[str, Any] = {
    "schema": "tabfree/model@0",
    "id": "p",
    "connection": {"engine": "athena", "database": "main"},
    "tables": [
        {
            "id": "orders",
            "source": {"table": "orders"},
            "primaryKey": ["id"],
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "region", "type": "string"},
                {"name": "customer", "type": "string"},
                {"name": "sales", "type": "float"},
            ],
        },
        {
            "id": "targets",
            "source": {"table": "targets"},
            "primaryKey": ["region"],
            "columns": [{"name": "region", "type": "string"}, {"name": "target", "type": "float"}],
        },
    ],
    "relationships": [
        {
            "left": "orders",
            "right": "targets",
            "on": [{"left": "region", "right": "region"}],
            "cardinality": "many_to_one",
        }
    ],
    "fields": [
        {
            "id": "region",
            "name": "Region",
            "role": "dimension",
            "type": "string",
            "table": "orders",
            "column": "region",
        },
        {
            "id": "customer",
            "name": "Customer",
            "role": "dimension",
            "type": "string",
            "table": "orders",
            "column": "customer",
        },
        {
            "id": "sales",
            "name": "Sales",
            "role": "measure",
            "type": "float",
            "table": "orders",
            "column": "sales",
            "defaultAggregation": "sum",
        },
        {
            "id": "target",
            "name": "Target",
            "role": "measure",
            "type": "float",
            "table": "targets",
            "column": "target",
            "defaultAggregation": "sum",
        },
    ],
    "parameters": [{"id": "top", "name": "Top", "type": "integer", "default": 1}],
    "groups": [
        {
            "id": "coasts",
            "name": "Coasts",
            "field": "Region",
            "groups": {"Coast": ["East", "West"]},
            "otherLabel": "Inland",
        }
    ],
    "bins": [{"id": "sales_bin", "name": "Sales (bin)", "field": "Sales", "size": 100}],
    "sets": [
        {"id": "vip", "name": "VIP", "field": "Customer", "members": ["Ann"]},
        {
            "id": "top_customers",
            "name": "Top Customers",
            "field": "Customer",
            "top": {"n": {"param": "top"}, "by": "SUM([Sales])"},
        },
        {"id": "big", "name": "Big Customers", "field": "Customer", "condition": "SUM([Sales]) >= 100"},
    ],
}


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute("CREATE TABLE main.orders (id INTEGER, region VARCHAR, customer VARCHAR, sales DOUBLE)")
    db.executemany("INSERT INTO main.orders VALUES (?, ?, ?, ?)", ORDERS)
    db.execute("CREATE TABLE main.targets (region VARCHAR, target DOUBLE)")
    db.executemany("INSERT INTO main.targets VALUES (?, ?)", TARGETS)
    return db


def run(con: Any, query: dict[str, Any], **kw: Any) -> list[dict[str, Any]]:
    compiled = compile_query(MODEL, query, dialect="duckdb", **kw)
    cursor = con.execute(compiled.sql, list(compiled.parameters))
    names = [d[0] for d in cursor.description]
    assert names == [c.name for c in compiled.columns]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def by(rows: list[dict[str, Any]], key: str, value: str) -> dict[Any, Any]:
    return {r[key]: r[value] for r in rows}


def test_groups_and_bins(con: Any) -> None:
    rows = run(con, {"dimensions": ["Coasts"], "measures": ["Sales"]})
    assert by(rows, "Coasts", "Sales") == {"Coast": 470.0, "Inland": 10.0}
    rows = run(
        con, {"dimensions": ["Sales (bin)"], "measures": [{"field": "Sales", "agg": "count", "as": "N"}]}
    )
    assert by(rows, "Sales (bin)", "N") == {0.0: 3, 100.0: 1, 300.0: 1}


def test_member_top_and_condition_sets(con: Any) -> None:
    assert by(run(con, {"dimensions": ["VIP"], "measures": ["Sales"]}), "VIP", "Sales") == {
        True: 120.0,
        False: 360.0,
    }
    rows = run(con, {"dimensions": ["Customer", "Top Customers"]})
    assert {r["Customer"] for r in rows if r["Top Customers"]} == {"Cal"}
    rows = run(con, {"dimensions": ["Customer", "Top Customers"]}, parameters={"top": 2})
    assert {r["Customer"] for r in rows if r["Top Customers"]} == {"Cal", "Ann"}
    rows = run(con, {"dimensions": ["Customer", "Big Customers"]})
    assert {r["Customer"] for r in rows if r["Big Customers"]} == {"Cal", "Ann"}


def test_measures_from_the_one_side_are_not_fanned_out(con: Any) -> None:
    rows = run(con, {"dimensions": ["Region"], "measures": ["Sales", "Target"]})
    assert by(rows, "Region", "Target") == {"East": 500.0, "West": 200.0, "Central": 100.0}  # not 1000/400
    [total] = run(con, {"measures": ["Target", "Sales"]})
    assert total == {"Target": 800.0, "Sales": 480.0}


def test_totals_are_re_aggregated_not_summed(con: Any) -> None:
    query = {
        "dimensions": ["Region", "Customer"],
        "measures": ["Sales", {"field": "Customer", "agg": "countd", "as": "Customers"}],
        "totals": {"grand": True, "subtotals": ["Region"]},
    }
    rows = run(con, query)
    grand = [r for r in rows if r["__total"] == "grand"]
    assert grand == [{"Region": None, "Customer": None, "Sales": 480.0, "Customers": 4, "__total": "grand"}]
    subtotals = {r["Region"]: r for r in rows if r["__total"] == "Region"}
    assert subtotals["East"]["Customers"] == 2
    assert subtotals["West"]["Sales"] == 320.0
    assert len([r for r in rows if r["__total"] is None]) == 5


def test_measure_names_and_values(con: Any) -> None:
    rows = run(con, {"dimensions": ["Region"], "measures": ["Sales", "Target"], "pivotMeasures": True})
    assert {(r["Region"], r["Measure Names"]): r["Measure Values"] for r in rows}[("East", "Target")] == 500.0
    assert len(rows) == 6


def test_computed_sort_by_an_aggregate_not_in_the_view(con: Any) -> None:
    rows = run(
        con, {"dimensions": ["Customer"], "sort": [{"by": "Customer", "expr": "SUM([Sales])", "dir": "desc"}]}
    )
    assert [r["Customer"] for r in rows] == ["Cal", "Ann", "Bob", "Dee"]


def test_manual_sort_lists_members_first(con: Any) -> None:
    rows = run(con, {"dimensions": ["Customer"], "sort": [{"by": "Customer", "order": ["Dee", "Bob"]}]})
    assert [r["Customer"] for r in rows] == ["Dee", "Bob", "Ann", "Cal"]


def test_unlinked_or_keyless_related_measures_are_refused(con: Any) -> None:
    keyless = {**MODEL, "tables": [MODEL["tables"][0], {**MODEL["tables"][1], "primaryKey": None}]}
    keyless["tables"][1].pop("primaryKey")
    with pytest.raises(UnsupportedError, match="primaryKey"):
        compile_query(keyless, {"dimensions": ["Region"], "measures": ["Target"]})
    with pytest.raises(CompileError, match="unknown field"):
        compile_query({**MODEL, "sets": []}, {"dimensions": ["Top Customers"]})


QUOTAS = [("East", 400.0), ("West", 100.0), ("North", 9.0)]
SECONDARY: dict[str, Any] = {
    "schema": "tabfree/model@0",
    "id": "quotas",
    "connection": {"engine": "athena", "database": "main"},
    "tables": [
        {
            "id": "q",
            "source": {"table": "quotas"},
            "columns": [{"name": "area", "type": "string"}, {"name": "quota", "type": "float"}],
        }
    ],
    "fields": [
        {"id": "area", "name": "Area", "role": "dimension", "type": "string", "table": "q", "column": "area"},
        {
            "id": "quota",
            "name": "Quota",
            "role": "measure",
            "type": "float",
            "table": "q",
            "column": "quota",
            "defaultAggregation": "sum",
        },
    ],
}


def test_blending_left_joins_the_secondary_at_the_link_level(con: Any) -> None:
    from tabfree_compiler.blend import compile_blend

    con.execute("CREATE TABLE IF NOT EXISTS main.quotas (area VARCHAR, quota DOUBLE)")
    con.execute("DELETE FROM main.quotas")
    con.executemany("INSERT INTO main.quotas VALUES (?, ?)", QUOTAS)
    query = {
        "dimensions": ["Region"],
        "measures": ["Sales"],
        "where": [{"field": "Region", "kind": "categorical", "members": ["East", "Central"]}],
        "blend": [
            {"model": "q", "measures": ["Quota"], "links": [{"primary": "Region", "secondary": "Area"}]}
        ],
    }
    compiled = compile_blend({"p": MODEL, "q": SECONDARY}, "p", query, dialect="duckdb")
    rows = con.execute(compiled.sql, list(compiled.parameters)).fetchall()
    assert [c.name for c in compiled.columns] == ["Region", "Sales", "Quota"]
    assert sorted(rows, key=str) == [("Central", 10.0, None), ("East", 150.0, 400.0)]  # no North, no West


def test_blend_errors() -> None:
    from tabfree_compiler.blend import compile_blend

    bad_link = {
        "dimensions": ["Region"],
        "measures": ["Sales"],
        "blend": [
            {"model": "q", "measures": ["Quota"], "links": [{"primary": "Customer", "secondary": "Area"}]}
        ],
    }
    with pytest.raises(CompileError, match="not a dimension"):
        compile_blend({"p": MODEL, "q": SECONDARY}, "p", bad_link)
    elsewhere = {**SECONDARY, "connection": {"engine": "redshift", "database": "dw", "workgroup": "w"}}
    query = {
        **bad_link,
        "blend": [{**bad_link["blend"][0], "links": [{"primary": "Region", "secondary": "Area"}]}],
    }
    with pytest.raises(UnsupportedError, match="another connection"):
        compile_blend({"p": MODEL, "q": elsewhere}, "p", query)
