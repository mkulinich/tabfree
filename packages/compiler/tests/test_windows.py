"""SQL window push-down of table calculations, checked on DuckDB against hand-computed values."""

from __future__ import annotations

from typing import Any

import pytest
import sqlglot

from tabfree_compiler import compile_query
from tabfree_compiler.sql import DIALECTS

duckdb = pytest.importorskip("duckdb")

MODEL: dict[str, Any] = {
    "schema": "tabfree/model@0",
    "id": "w",
    "connection": {"engine": "athena", "database": "main"},
    "tables": [
        {
            "id": "t",
            "source": {"table": "w"},
            "columns": [
                {"name": "r", "type": "string"},
                {"name": "m", "type": "integer"},
                {"name": "v", "type": "float"},
            ],
        }
    ],
    "fields": [
        {"id": "r", "name": "Region", "role": "dimension", "type": "string", "table": "t", "column": "r"},
        {"id": "m", "name": "Month", "role": "dimension", "type": "integer", "table": "t", "column": "m"},
        {
            "id": "v",
            "name": "Sales",
            "role": "measure",
            "type": "float",
            "table": "t",
            "column": "v",
            "defaultAggregation": "sum",
        },
    ],
}
ROWS = [("E", 1, 10.0), ("E", 2, 20.0), ("E", 3, 30.0), ("W", 1, 5.0), ("W", 2, None), ("W", 3, 15.0)]


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute("CREATE TABLE main.w (r VARCHAR, m INTEGER, v DOUBLE)")
    db.executemany("INSERT INTO main.w VALUES (?, ?, ?)", ROWS)
    return db


def run(
    con: Any, calcs: list[dict[str, Any]]
) -> tuple[dict[tuple[str, int], dict[str, Any]], tuple[str, ...]]:
    query = {
        "dimensions": ["Region", "Month"],
        "measures": ["Sales"],
        "tableCalcs": calcs,
        "sort": [{"by": "Month"}],
    }
    compiled = compile_query(MODEL, query, dialect="duckdb", table_calcs="sql")
    cursor = con.execute(compiled.sql, list(compiled.parameters))
    names = [d[0] for d in cursor.description]
    assert names == [c.name for c in compiled.columns]
    rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    return {(r["Region"], r["Month"]): r for r in rows}, compiled.deferred_table_calcs


def along_month(name: str, type_: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": type_,
        "of": "Sales",
        "computeUsing": "specific",
        "addressing": ["Month"],
        **extra,
    }


def test_running_moving_and_totals(con: Any) -> None:
    rows, deferred = run(
        con,
        [
            along_month("Run", "running_sum"),
            along_month("Avg2", "moving_avg", frame={"start": -1, "end": 0}),
            along_month("Total", "total"),
            along_month("Share", "percent_of_total"),
        ],
    )
    assert deferred == ()
    assert [rows[("E", m)]["Run"] for m in (1, 2, 3)] == [10, 30, 60]
    assert [rows[("W", m)]["Run"] for m in (1, 2, 3)] == [5, 5, 20]  # NULLs are skipped, as in Tableau
    assert rows[("E", 2)]["Avg2"] == pytest.approx(15)
    assert rows[("W", 2)]["Avg2"] == pytest.approx(5)
    assert rows[("E", 1)]["Total"] == 60
    assert rows[("E", 3)]["Share"] == pytest.approx(0.5)
    assert rows[("W", 2)]["Share"] is None


def test_differences_lookups_and_positions(con: Any) -> None:
    rows, _ = run(
        con,
        [
            along_month("Diff", "difference"),
            along_month("PctFirst", "percent_difference", relativeTo="first"),
            along_month("Next", "lookup", offset=1),
            {"name": "Index", "type": "index", "computeUsing": "specific", "addressing": ["Month"]},
            {"name": "Size", "type": "size", "computeUsing": "specific", "addressing": ["Month"]},
            {"name": "Last", "type": "last", "computeUsing": "specific", "addressing": ["Month"]},
        ],
    )
    assert [rows[("E", m)]["Diff"] for m in (1, 2, 3)] == [None, 10, 10]
    assert [rows[("W", m)]["Diff"] for m in (1, 2, 3)] == [None, -5, 15]  # ZN: a missing month counts as 0
    assert rows[("E", 3)]["PctFirst"] == pytest.approx(2.0)
    assert rows[("E", 1)]["Next"] == 20
    assert rows[("E", 3)]["Next"] is None
    assert [rows[("W", m)]["Index"] for m in (1, 2, 3)] == [1, 2, 3]
    assert rows[("W", 1)]["Size"] == 3
    assert [rows[("E", m)]["Last"] for m in (1, 2, 3)] == [2, 1, 0]


def test_ranks_across_the_table_and_null_handling(con: Any) -> None:
    rows, _ = run(
        con,
        [
            {"name": "Rank", "type": "rank", "of": "Sales", "computeUsing": "table_down"},
            {
                "name": "Asc",
                "type": "rank_unique",
                "of": "Sales",
                "computeUsing": "table_down",
                "direction": "asc",
            },
        ],
    )
    assert rows[("E", 3)]["Rank"] == 1
    assert rows[("W", 1)]["Rank"] == 5
    assert rows[("W", 2)]["Rank"] is None
    assert rows[("W", 1)]["Asc"] == 1


def test_unpushable_calcs_are_deferred(con: Any) -> None:
    rows, deferred = run(
        con,
        [
            along_month("Run", "running_sum"),
            {"name": "Custom", "type": "custom", "expr": "WINDOW_AVG(SUM([Sales]), -2, 0)"},
            along_month("Then", "running_sum", then={"type": "percent_of_total"}),
        ],
    )
    assert deferred == ("Custom", "Then")
    assert "Custom" not in rows[("E", 1)]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_push_down_renders_in_every_dialect(dialect: str) -> None:
    query = {
        "dimensions": ["Region", "Month"],
        "measures": ["Sales"],
        "tableCalcs": [
            along_month("Run", "running_sum"),
            along_month("Diff", "difference"),
            {"name": "R", "type": "rank", "of": "Sales"},
        ],
    }
    compiled = compile_query(MODEL, query, dialect=dialect, table_calcs="sql")  # type: ignore[arg-type]
    sql = compiled.sql if dialect == "redshift" else compiled.sql.replace("?", "NULL")
    assert "OVER" in sql
    sqlglot.parse_one(sql, read="trino" if dialect == "athena" else dialect)


def test_runtime_mode_is_the_default() -> None:
    compiled = compile_query(
        MODEL,
        {"dimensions": ["Region"], "measures": ["Sales"], "tableCalcs": [{"name": "I", "type": "index"}]},
    )
    assert "OVER" not in compiled.sql
    assert compiled.deferred_table_calcs == ("I",)
