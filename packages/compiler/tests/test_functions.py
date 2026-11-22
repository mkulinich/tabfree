"""P2.3 functions beyond the TDVT suites, executed on DuckDB, plus per-dialect translation checks."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import sqlglot

from tabfree_compiler import Session, UnsupportedError, compile_expression, compile_query
from tabfree_compiler.sql import DIALECTS, translate_date_pattern
from tabfree_compiler.tdvt import calcs_model

duckdb = pytest.importorskip("duckdb")

MODEL: dict[str, Any] = {
    "schema": "tabfree/model@0",
    "id": "t",
    "connection": {"engine": "athena", "database": "main"},
    "settings": {"fiscalYearStartMonth": 7},
    "tables": [
        {
            "id": "t",
            "source": {"table": "t"},
            "columns": [
                {"name": "s", "type": "string"},
                {"name": "d", "type": "date"},
                {"name": "x", "type": "float"},
                {"name": "y", "type": "float"},
            ],
        }
    ],
    "fields": [
        {"id": "s", "name": "S", "role": "dimension", "type": "string", "table": "t", "column": "s"},
        {"id": "d", "name": "D", "role": "dimension", "type": "date", "table": "t", "column": "d"},
        {"id": "x", "name": "X", "role": "measure", "type": "float", "table": "t", "column": "x"},
        {"id": "y", "name": "Y", "role": "measure", "type": "float", "table": "t", "column": "y"},
    ],
}


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute("CREATE TABLE main.t (s VARCHAR, d DATE, x DOUBLE, y DOUBLE)")
    db.executemany(
        "INSERT INTO main.t VALUES (?, ?, ?, ?)",
        [
            ("a,b,a,c", dt.date(2023, 6, 30), 1.0, 2.0),
            ("hello wORLD", dt.date(2023, 7, 1), 2.0, 4.5),
            ("x", dt.date(2024, 1, 15), 3.0, 5.0),
        ],
    )
    return db


def value(con: Any, formula: str, session: Session | None = None) -> list[Any]:
    q = compile_expression(MODEL, formula, dialect="duckdb", session=session)
    return sorted((r[0] for r in con.execute(q.sql, list(q.parameters)).fetchall()), key=str)


def test_findnth_and_proper(con: Any) -> None:
    assert value(con, "FINDNTH('a,b,a,c', 'a', 2)") == [5]
    assert value(con, "FINDNTH('a,b,a,c', 'a', 3)") == [0]
    assert value(con, "PROPER('hello wORLD')") == ["Hello World"]


def test_makedatetime_and_maketime(con: Any) -> None:
    assert value(con, "MAKEDATETIME(#2024-02-03#, #1899-12-30 13:45:10#)") == [
        dt.datetime(2024, 2, 3, 13, 45, 10)
    ]
    assert value(con, "MAKETIME(5, 6, 7)") == [dt.datetime(1899, 12, 30, 5, 6, 7)]


def test_correlation_and_covariance(con: Any) -> None:
    [corr] = value(con, "CORR([X], [Y])")
    [covar] = value(con, "COVAR([X], [Y])")
    [covarp] = value(con, "COVARP([X], [Y])")
    assert corr == pytest.approx(1.5 / (31 / 12) ** 0.5)  # cov / (sd_x * sd_y), sd_x = 1
    assert covar == pytest.approx(1.5)
    assert covarp == pytest.approx(1.0)


def test_fiscal_years_on_shelves_not_in_formulas(con: Any) -> None:
    q = compile_query(
        MODEL, {"dimensions": [{"field": "D", "datePart": "year", "as": "FY"}]}, dialect="duckdb"
    )
    assert sorted(r[0] for r in con.execute(q.sql).fetchall()) == [
        2023,
        2024,
    ]  # Jun 2023 → FY2023, Jul 2023 → FY2024
    q = compile_query(
        MODEL, {"dimensions": [{"field": "D", "dateTrunc": "year", "as": "FY start"}]}, dialect="duckdb"
    )
    starts = sorted(r[0] for r in con.execute(q.sql).fetchall())
    assert starts == [dt.datetime(2022, 7, 1), dt.datetime(2023, 7, 1)]
    assert value(con, "DATEPART('year', [D])") == [2023, 2024]  # formulas stay calendar-based


def test_userattributeincludes_binds_values(con: Any) -> None:
    session = Session(username="u", attributes={"regions": "East, West"})
    q = compile_expression(
        MODEL, "USERATTRIBUTEINCLUDES('regions', 'West')", dialect="duckdb", session=session
    )
    assert "East" not in q.sql  # the viewer's attribute values are bind parameters
    assert [r[0] for r in con.execute(q.sql, list(q.parameters)).fetchall()] == [True]


def test_unsupported_functions_explain_why() -> None:
    with pytest.raises(UnsupportedError, match="pass-through SQL"):
        compile_expression(MODEL, "RAWSQL_INT('1')")
    with pytest.raises(UnsupportedError, match="analytics extensions"):
        compile_expression(MODEL, "SCRIPT_REAL('x', SUM([X]))")


def test_date_patterns_per_dialect() -> None:
    assert translate_date_pattern("MM/dd/yyyy hh:mm:ss a", "duckdb") == "%m/%d/%Y %I:%M:%S %p"
    assert translate_date_pattern("MM/dd/yyyy hh:mm:ss a", "athena") == "%m/%d/%Y %h:%i:%s %p"
    assert translate_date_pattern("MM/dd/yyyy hh:mm:ss a", "redshift") == "MM/DD/YYYY HH12:MI:SS AM"
    assert translate_date_pattern("dd 'de' MMMM", "duckdb") == "%d de %B"


@pytest.mark.parametrize("dialect", DIALECTS)
def test_new_functions_render_in_every_dialect(dialect: str) -> None:
    model = calcs_model()
    for formula in (
        "REGEXP_MATCH([str1], 'BI.*')",
        "REGEXP_EXTRACT([str1], '(\\w+) ')",
        "REGEXP_REPLACE([str1], 'A', 'b')",
        "DATEPARSE('yyyy-MM-dd', [str2])",
        "PERCENTILE([num0], 0.25)",
        "DATEADD('iso-quarter', 1, [date0])",
        "DATETRUNC('iso-year', [date0])",
        "CORR([num0], [num1])",
    ):
        compiled = compile_expression(model, formula, dialect=dialect)  # type: ignore[arg-type]
        sql = compiled.sql if dialect == "redshift" else compiled.sql.replace("?", "NULL")
        sqlglot.parse_one(sql, read="trino" if dialect == "athena" else dialect)


@pytest.mark.parametrize("dialect", ["athena", "trino"])
def test_trino_function_names(dialect: str) -> None:
    """Found by running TDVT on Trino: these names do not exist there, or mean something else."""
    model = calcs_model()

    def sql(formula: str) -> str:
        return compile_expression(model, formula, dialect=dialect).sql.upper()  # type: ignore[arg-type]

    div = sql("DIV([int0], [int1])")
    assert "TRUNCATE(" in div
    assert "TRUNC(" not in div
    space = sql("SPACE([int2])")
    assert "RPAD(" in space
    assert "REPEAT(" not in space
    ascii_ = sql("ASCII([str1])")
    assert "CODEPOINT(" in ascii_
    assert "UNICODE(" not in ascii_
    median = sql("MEDIAN([num0])")
    assert "APPROX_PERCENTILE(" in median
    assert "PERCENTILE_CONT" not in median
    assert "MEDIAN(" not in median
    date = sql("DATE([str2])")
    assert "COALESCE(TRY_CAST(" in date
    assert "AS TIMESTAMP" in date


@pytest.mark.parametrize("dialect", ["duckdb", "redshift"])
def test_other_dialects_keep_their_function_names(dialect: str) -> None:
    model = calcs_model()

    def sql(formula: str) -> str:
        return compile_expression(model, formula, dialect=dialect).sql.upper()  # type: ignore[arg-type]

    assert "TRUNC(" in sql("DIV([int0], [int1])")
    assert "REPEAT(" in sql("SPACE([int2])")
    assert "CODEPOINT(" not in sql("ASCII([str1])")
