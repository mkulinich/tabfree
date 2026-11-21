"""Per-column parity, with DuckDB over Parquet standing in for both Hyper and Athena (P2.8)."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot
from typer.testing import CliRunner

from tabfree_migrate.cli import app
from tabfree_migrate.extract_types import ParityKind
from tabfree_migrate.hyper import ColumnInfo, TableResult
from tabfree_migrate.parity import (
    AthenaRunner,
    TableStats,
    athena_table,
    compare_stats,
    duckdb_parquet_runner,
    measure,
    normalise_timestamp,
    normalise_wkt,
    parse_stats,
    stats_for,
    stats_query,
)

COLUMNS = [
    ColumnInfo("Row ID", "row_id", "BIG_INT", "bigint", False),
    ColumnInfo("Profit", "profit", "NUMERIC", "decimal(18,2)", True, parity="decimal", scale=2),
    ColumnInfo("Sales", "sales", "DOUBLE", "double", True),
    ColumnInfo("Discount", "discount", "FLOAT", "float", True),
    ColumnInfo("Returned", "returned", "BOOL", "boolean", True),
    ColumnInfo("Region", "region", "TEXT", "string", True),
    ColumnInfo("Order Date", "order_date", "DATE", "date", True),
    ColumnInfo("Shipped At", "shipped_at", "TIMESTAMP", "timestamp", True),
    ColumnInfo("Logged At", "logged_at", "TIMESTAMP_TZ", "timestamp", True),
    ColumnInfo("Location", "location", "GEOGRAPHY", "string", True),
    ColumnInfo("Blob", "blob", "BYTES", "binary", True),
]

CREATE = """
CREATE TABLE "Extract" (
  "Row ID" BIGINT NOT NULL, "Profit" DECIMAL(18,2), "Sales" DOUBLE, "Discount" FLOAT,
  "Returned" BOOLEAN, "Region" VARCHAR, "Order Date" DATE, "Shipped At" TIMESTAMP,
  "Logged At" TIMESTAMPTZ, "Location" VARCHAR, "Blob" BLOB
);
INSERT INTO "Extract" VALUES
  (1, 10.25, 100.5, 0.1, true, 'East', DATE '2024-01-02', TIMESTAMP '2024-01-02 10:00:00.123456',
   TIMESTAMPTZ '2024-01-02 10:00:00+02', 'POINT (1 2)', 'a'::BLOB),
  (2, -3.10, 0.1, 0.2, false, 'West', DATE '2024-03-04', TIMESTAMP '2024-03-04 00:00:00',
   TIMESTAMPTZ '2024-03-04 00:00:00+00', 'POLYGON ((0 0, 1 0, 1 1, 0 0))', NULL),
  (9007199254740993, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL);
"""

EXPORT = ", ".join(f'"{c.name}" AS "{c.glue_name}"' for c in COLUMNS)


@pytest.fixture
def converted(tmp_path: Path) -> tuple[duckdb.DuckDBPyConnection, Path]:
    """An in-memory "extract" (the Hyper stand-in) and the Parquet a converter would write."""
    conn = duckdb.connect()
    conn.execute("SET TimeZone = 'UTC'")
    conn.execute(CREATE)
    parquet = tmp_path / "extract.parquet"
    conn.execute(f"COPY (SELECT {EXPORT} FROM \"Extract\") TO '{parquet}' (FORMAT PARQUET)")
    return conn, parquet


def _runner(conn: duckdb.DuckDBPyConnection) -> Any:
    def run(sql: str) -> Sequence[Any]:
        # Text results, like Athena's API (and no pytz needed for zoned timestamps).
        row = conn.execute(f"SELECT COLUMNS(*)::VARCHAR FROM ({sql})").fetchone()
        assert row is not None
        return row

    return run


def _expected(conn: duckdb.DuckDBPyConnection) -> TableStats:
    # The hyper-dialect query (original names, Postgres type names) also runs in DuckDB.
    return measure(_runner(conn), '"Extract"', COLUMNS, "hyper")


def test_identical_copy_passes_every_check(converted: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    conn, parquet = converted
    expected = _expected(conn)
    actual = measure(duckdb_parquet_runner({"orders": str(parquet)}), '"orders"', COLUMNS, "duckdb")
    result = compare_stats(expected, actual, COLUMNS)
    assert result.ok, result.to_dict()
    assert not result.warnings
    assert expected.rows == 3
    assert expected.columns["row_id"]["sum"] == "9007199254740996"  # exact beyond 2**53
    assert expected.columns["profit"]["sum"] == "7.15"
    assert expected.columns["returned"]["true_count"] == 1
    assert expected.columns["logged_at"]["min"] == "2024-01-02 08:00:00.000000"  # UTC instant
    assert expected.columns["shipped_at"]["max"] == "2024-03-04 00:00:00.000000"
    assert expected.columns["location"]["max"] == "POLYGON((0 0,1 0,1 1,0 0))"
    assert expected.columns["blob"] == {"count": 1, "nulls": 2}
    assert expected.columns["region"]["nulls"] == 1


def test_lost_row_and_changed_values_are_reported(
    converted: tuple[duckdb.DuckDBPyConnection, Path], tmp_path: Path
) -> None:
    conn, _ = converted
    expected = _expected(conn)
    conn.execute("""DELETE FROM "Extract" WHERE "Row ID" = 9007199254740993""")
    conn.execute("""UPDATE "Extract" SET "Region" = 'east', "Profit" = 10.26 WHERE "Row ID" = 1""")
    damaged = tmp_path / "damaged.parquet"
    conn.execute(f"COPY (SELECT {EXPORT} FROM \"Extract\") TO '{damaged}' (FORMAT PARQUET)")
    actual = measure(duckdb_parquet_runner({"t": str(damaged)}), '"t"', COLUMNS, "duckdb")
    result = compare_stats(expected, actual, COLUMNS)
    failed = {(c.column, c.stat) for c in result.errors}
    assert not result.ok
    assert {(None, "rows"), ("row_id", "count"), ("row_id", "sum"), ("profit", "sum")} <= failed
    assert ("region", "min") in failed  # binary collation: text is exact
    assert ("sales", "sum") not in failed  # the dropped row held NULLs only


def test_float_tolerance_and_nan() -> None:
    cols = [ColumnInfo("x", "x", "DOUBLE", "double", True), ColumnInfo("y", "y", "FLOAT", "float", True)]
    base = TableStats(1, {"x": {"sum": 1.0e12, "min": math.nan}, "y": {"sum": 1.0}})
    near = TableStats(1, {"x": {"sum": 1.0e12 * (1 + 1e-12), "min": math.nan}, "y": {"sum": 1.0000001}})
    far = TableStats(1, {"x": {"sum": 1.0e12 * (1 + 1e-6), "min": 0.0}, "y": {"sum": 1.001}})
    assert compare_stats(base, near, cols).ok
    errors = {(c.column, c.stat, c.rule) for c in compare_stats(base, far, cols).errors}
    assert errors == {
        ("x", "sum", "float-tolerance"),
        ("x", "min", "float-tolerance"),
        ("y", "sum", "float32-tolerance"),
    }


def test_non_binary_collation_makes_text_checks_advisory() -> None:
    col = ColumnInfo("Name", "name", "TEXT", "string", True, collation="en_US_CI")
    expected = TableStats(2, {"name": {"count": 2, "distinct": 1, "min": "Anna", "max": "anna"}})
    actual = TableStats(2, {"name": {"count": 2, "distinct": 2, "min": "Anna", "max": "anna"}})
    result = compare_stats(expected, actual, [col])
    assert result.ok
    assert [(c.stat, c.severity) for c in result.warnings] == [("distinct", "warning")]
    actual.columns["name"]["count"] = 1
    assert not compare_stats(expected, actual, [col]).ok  # counts stay exact


def test_missing_column_is_an_error() -> None:
    col = ColumnInfo("a", "a", "INT", "integer", True)
    result = compare_stats(TableStats(1, {"a": {"count": 1}}), TableStats(1, {}), [col])
    assert [(c.column, c.stat) for c in result.errors] == [("a", "present")]


def test_normalisation() -> None:
    assert normalise_timestamp("2024-01-02 03:04:05.678 UTC") == "2024-01-02 03:04:05.678000"
    assert normalise_timestamp("2024-01-02T03:04:05Z") == "2024-01-02 03:04:05.000000"
    zoned = datetime(2024, 1, 2, 5, 0, tzinfo=timezone(timedelta(hours=2)))
    assert normalise_timestamp(zoned) == "2024-01-02 03:00:00.000000"
    assert normalise_timestamp(datetime(2024, 1, 2, 3, 4, 5, 678901, tzinfo=UTC), "ms") == (
        "2024-01-02 03:04:05.678000"
    )
    assert normalise_timestamp(date(2024, 1, 2)) == "2024-01-02 00:00:00.000000"
    assert normalise_wkt("  point( 1   2 ) ") == "POINT(1 2)"
    assert normalise_wkt("MULTIPOLYGON (((0 0, 1 1)))") == "MULTIPOLYGON(((0 0,1 1)))"


def test_hyper_api_values_are_unwrapped() -> None:
    class HyperTimestamp:  # tableauhyperapi.Timestamp has to_datetime()
        def to_datetime(self) -> datetime:
            return datetime(2024, 5, 6, 7, 8, 9)

    col = ColumnInfo("t", "t", "TIMESTAMP", "timestamp", True)
    query = stats_query('"x"', [col], "hyper")
    stats = parse_stats(query, [1, 1, 1, HyperTimestamp(), HyperTimestamp()], [col])
    assert stats.columns["t"]["min"] == "2024-05-06 07:08:09.000000"
    with pytest.raises(ValueError, match="expected"):
        parse_stats(query, [1], [col])


def test_stats_per_kind() -> None:
    assert stats_for(ParityKind.BINARY) == ["count"]
    kinds = {c.glue_name: stats_for(ParityKind(c.parity)) for c in COLUMNS}
    assert kinds["returned"] == ["count", "distinct", "true_count"]
    assert kinds["sales"] == ["count", "distinct", "sum", "min", "max"]
    assert kinds["location"] == ["count", "distinct", "min", "max"]


def test_athena_sql_parses_as_trino_and_casts_overflow_safely() -> None:
    query = stats_query(athena_table("tabfree_lake", "orders"), COLUMNS, "athena")
    assert sqlglot.parse_one(query.sql, read="trino") is not None
    assert 'FROM "tabfree_lake"."orders"' in query.sql
    assert 'SUM(CAST("row_id" AS DECIMAL(38,0)))' in query.sql
    assert 'SUM(CAST("profit" AS DECIMAL(38,2)))' in query.sql
    assert 'CAST(MIN("logged_at") AS VARCHAR)' in query.sql
    assert '"Location"' not in query.sql  # Athena sees Glue names only
    hyper = stats_query('"Extract"."Extract"', COLUMNS, "hyper")
    assert 'MIN(CAST("Location" AS TEXT))' in hyper.sql
    assert "DOUBLE PRECISION" in hyper.sql
    assert sqlglot.parse_one(hyper.sql, read="postgres") is not None
    assert len(query.keys) == len(hyper.keys)


class FakeAthena:
    def __init__(self, states: list[str], row: list[str | None]) -> None:
        self.states = states
        self.row = row
        self.sql = ""

    def start_query_execution(self, QueryString: str, WorkGroup: str) -> dict[str, str]:  # noqa: N803
        self.sql = QueryString
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId: str) -> dict[str, Any]:  # noqa: N803
        return {"QueryExecution": {"Status": {"State": self.states.pop(0), "StateChangeReason": "boom"}}}

    def get_query_results(self, QueryExecutionId: str) -> dict[str, Any]:  # noqa: N803
        header = {"Data": [{"VarCharValue": "s0"}]}
        data = {"Data": [{"VarCharValue": v} if v is not None else {} for v in self.row]}
        return {"ResultSet": {"Rows": [header, data]}}


def test_athena_runner_polls_and_returns_strings() -> None:
    col = ColumnInfo("d", "d", "NUMERIC", "decimal(10,2)", True, parity="decimal", scale=2)
    fake = FakeAthena(["QUEUED", "RUNNING", "SUCCEEDED"], ["3", "2", "2", "12.50", "1.00", None])
    stats = measure(AthenaRunner(fake, "wg", sleep=lambda _s: None), '"db"."t"', [col], "athena")
    assert stats.columns["d"] == {
        "count": 2,
        "distinct": 2,
        "sum": "12.5",
        "min": "1",
        "max": None,
        "nulls": 1,
    }
    assert Decimal(str(stats.columns["d"]["sum"])) == Decimal("12.50")
    with pytest.raises(RuntimeError, match="FAILED: boom"):
        AthenaRunner(FakeAthena(["FAILED"], []), "wg", sleep=lambda _s: None)("SELECT 1")
    with pytest.raises(TimeoutError):
        AthenaRunner(
            FakeAthena(["RUNNING"] * 5, []), "wg", poll_seconds=1, timeout_seconds=2, sleep=lambda _s: None
        )("SELECT 1")


def test_cli_parity_check_against_local_parquet(
    converted: tuple[duckdb.DuckDBPyConnection, Path], tmp_path: Path
) -> None:
    conn, parquet = converted
    root = tmp_path / "out"
    key = "extracts/abc/orders/data.parquet"
    (root / key).parent.mkdir(parents=True)
    (root / key).write_bytes(parquet.read_bytes())
    table = TableResult("Extract", "Extract", "orders", 3, COLUMNS, key, _expected(conn).to_dict())
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"tables": [asdict(table)]}, default=str), encoding="utf-8")
    runner = CliRunner()
    ok = runner.invoke(app, ["parity-check", str(report), "--root", str(root)])
    assert ok.exit_code == 0, ok.output
    assert "ok   orders" in ok.output

    table.stats["columns"]["sales"]["sum"] = 1.0
    report.write_text(json.dumps({"tables": [asdict(table)]}, default=str), encoding="utf-8")
    bad = runner.invoke(app, ["parity-check", str(report), "--root", str(root)])
    assert bad.exit_code == 1
    assert "sales.sum (float-tolerance)" in bad.output
    assert runner.invoke(app, ["parity-check", str(report), "--engine", "athena"]).exit_code == 2
    assert runner.invoke(app, ["parity-check", str(report), "--engine", "x"]).exit_code == 2
