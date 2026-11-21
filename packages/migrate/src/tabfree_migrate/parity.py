"""Per-column parity between a Hyper extract and its Athena/Iceberg copy (BUILD_PLAN P2.8).

One query per table computes, for every column, the statistics that its parity kind supports
(:class:`tabfree_migrate.extract_types.ParityKind`). The same statistics are generated in three
dialects: ``hyper`` (run by the converter on the original extract), ``athena`` (run on the
Iceberg table) and ``duckdb`` (the stand-in for Athena in tests and local checks, reading the
Parquet the converter wrote).

| Statistic | Kinds | Rule |
|---|---|---|
| rows, count (non-null), nulls | all | exact |
| distinct | all but binary | exact; advisory (warning) for text under a non-binary collation |
| sum | integer, decimal | exact (decimal arithmetic) |
| sum, min, max | float, float32 | relative tolerance (1e-9; 1e-6 for float32); NaN equals NaN |
| true_count | boolean | exact |
| min, max | integer, decimal | exact |
| min, max | date | exact on the ISO date |
| min, max | timestamp, timestamp_tz | normalised: UTC, naive, truncated to the compared precision |
| min, max | text | exact; advisory under a non-binary collation |
| min, max | wkt | normalised: upper case, whitespace collapsed |

Values are normalised when a result row is parsed, so reports hold JSON-safe strings and numbers:
integers and decimals as strings (no float rounding), timestamps as ISO text.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from tabfree_migrate.extract_types import ParityKind, is_binary_collation

Dialect = Literal["hyper", "athena", "duckdb"]
Value = int | float | str | None

FLOAT_REL_TOL = 1e-9
FLOAT32_REL_TOL = 1e-6

_NUMERIC_KINDS = {ParityKind.INTEGER, ParityKind.DECIMAL, ParityKind.FLOAT, ParityKind.FLOAT32}
_TIME_KINDS = {ParityKind.TIMESTAMP, ParityKind.TIMESTAMP_TZ}
_WS = re.compile(r"\s+")
_WKT_PUNCT = re.compile(r"\s*([(),])\s*")


class ParityColumn(Protocol):
    """What parity needs to know about a column (``hyper.ColumnInfo`` satisfies it)."""

    @property
    def name(self) -> str: ...

    @property
    def glue_name(self) -> str: ...

    @property
    def parity(self) -> str: ...

    @property
    def export_as_text(self) -> bool: ...

    @property
    def collation(self) -> str | None: ...

    @property
    def scale(self) -> int | None: ...


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _types(dialect: Dialect) -> dict[str, str]:
    if dialect == "hyper":
        return {"double": "DOUBLE PRECISION", "decimal": "NUMERIC", "text": "TEXT"}
    return {"double": "DOUBLE", "decimal": "DECIMAL", "text": "VARCHAR"}


def stats_for(kind: ParityKind) -> list[str]:
    """Statistics computed for a parity kind (``rows`` is per table)."""
    stats = ["count"]
    if kind is not ParityKind.BINARY:
        stats.append("distinct")
    if kind in _NUMERIC_KINDS:
        stats += ["sum", "min", "max"]
    elif kind is ParityKind.BOOLEAN:
        stats.append("true_count")
    elif kind is not ParityKind.BINARY:
        stats += ["min", "max"]
    return stats


def _expression(stat: str, ref: str, kind: ParityKind, scale: int | None, dialect: Dialect) -> str:
    t = _types(dialect)
    if stat == "count":
        return f"COUNT({ref})"
    if stat == "distinct":
        return f"COUNT(DISTINCT {ref})"
    if stat == "true_count":
        return f"SUM(CASE WHEN {ref} THEN 1 ELSE 0 END)"
    if stat == "sum":
        if kind is ParityKind.INTEGER:
            # Athena's SUM(bigint) overflows; 38-digit decimals do not.
            return f"SUM(CAST({ref} AS {t['decimal']}(38,0)))"
        if kind is ParityKind.DECIMAL:
            return f"SUM(CAST({ref} AS {t['decimal']}(38,{scale or 0})))"
        return f"SUM(CAST({ref} AS {t['double']}))"
    agg = f"{stat.upper()}({ref})"
    if kind in {ParityKind.FLOAT, ParityKind.FLOAT32}:
        return f"CAST({agg} AS {t['double']})"
    if dialect != "hyper" and (kind in _TIME_KINDS or kind is ParityKind.DATE):
        # Athena returns text anyway; DuckDB would need pytz for zoned values.
        return f"CAST({agg} AS {t['text']})"
    return agg


@dataclass(frozen=True)
class StatsQuery:
    sql: str
    #: One ``(column glue name or None for the table, statistic)`` per output column, in order.
    keys: list[tuple[str | None, str]]


def stats_query(table_sql: str, columns: Sequence[ParityColumn], dialect: Dialect) -> StatsQuery:
    """One-row query computing every statistic for ``columns`` of ``table_sql`` (already quoted).

    In ``hyper`` the columns are referenced by their original names (cast to text when the export
    casts them); elsewhere by their Glue names, as written to Parquet.
    """
    keys: list[tuple[str | None, str]] = [(None, "rows")]
    parts = ["COUNT(*)"]
    for col in columns:
        kind = ParityKind(col.parity)
        if dialect == "hyper":
            ref = _q(col.name)
            if col.export_as_text:
                ref = f"CAST({ref} AS TEXT)"
        else:
            ref = _q(col.glue_name)
        for stat in stats_for(kind):
            keys.append((col.glue_name, stat))
            parts.append(_expression(stat, ref, kind, col.scale, dialect))
    select = ",\n  ".join(f"{expr} AS {_q(f's{i}')}" for i, expr in enumerate(parts))
    return StatsQuery(f"SELECT\n  {select}\nFROM {table_sql}", keys)


def athena_table(database: str, table: str) -> str:
    return f"{_q(database)}.{_q(table)}"


# --- normalisation -------------------------------------------------------------------------------


def _unwrap(value: Any) -> Any:
    """tableauhyperapi returns its own Date/Timestamp classes; turn them into stdlib values."""
    for attr in ("to_datetime", "to_date"):
        method = getattr(value, attr, None)
        if callable(method):
            return method()
    return value


def _exact_number(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral_value() and "e" not in str(value).lower():
        return str(number.quantize(Decimal(1)))
    return str(number.normalize())


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def parse_timestamp(value: Any) -> datetime | None:
    value = _unwrap(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if len(text) > 10 and text[10] == "T":
            text = f"{text[:10]} {text[11:]}"
        if text.endswith((" UTC", " Z")):
            text = text.rsplit(" ", 1)[0] + "+00:00"
        elif text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def normalise_timestamp(value: Any, precision: Literal["us", "ms", "s"] = "us") -> str | None:
    moment = parse_timestamp(value)
    if moment is None:
        return None
    if precision == "ms":
        moment = moment.replace(microsecond=moment.microsecond // 1000 * 1000)
    elif precision == "s":
        moment = moment.replace(microsecond=0)
    return moment.isoformat(sep=" ", timespec="microseconds")


def normalise_date(value: Any) -> str | None:
    value = _unwrap(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value).strip()[:10]).isoformat()


def normalise_wkt(value: Any) -> str | None:
    if value is None:
        return None
    text = _WS.sub(" ", str(value).strip().upper())
    return _WKT_PUNCT.sub(r"\1", text)


def _normalise(stat: str, kind: ParityKind, value: Any, precision: Literal["us", "ms", "s"]) -> Value:
    value = _unwrap(value)
    if value is None:
        return None
    if stat in {"rows", "count", "distinct", "true_count"}:
        return int(value)
    if kind in {ParityKind.FLOAT, ParityKind.FLOAT32}:
        return _float(value)
    if kind in {ParityKind.INTEGER, ParityKind.DECIMAL}:
        return _exact_number(value)
    if kind is ParityKind.DATE:
        return normalise_date(value)
    if kind in _TIME_KINDS:
        return normalise_timestamp(value, precision)
    if kind is ParityKind.WKT:
        return normalise_wkt(value)
    return str(value)


@dataclass
class TableStats:
    rows: int
    columns: dict[str, dict[str, Value]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TableStats:
        return cls(int(data["rows"]), {k: dict(v) for k, v in dict(data.get("columns", {})).items()})


def parse_stats(
    query: StatsQuery,
    row: Sequence[Any],
    columns: Sequence[ParityColumn],
    *,
    timestamp_precision: Literal["us", "ms", "s"] = "us",
) -> TableStats:
    """Turn the single result row of :func:`stats_query` into normalised :class:`TableStats`."""
    if len(row) != len(query.keys):
        raise ValueError(f"expected {len(query.keys)} values, got {len(row)}")
    kinds = {c.glue_name: ParityKind(c.parity) for c in columns}
    rows = int(row[0])
    stats = TableStats(rows, {c.glue_name: {} for c in columns})
    for (column, stat), value in zip(query.keys[1:], row[1:], strict=True):
        if column is None:
            raise ValueError("only the first statistic is per table")
        stats.columns[column][stat] = _normalise(stat, kinds[column], value, timestamp_precision)
    for values in stats.columns.values():
        if "count" in values and values["count"] is not None:
            values["nulls"] = rows - int(values["count"])
    return stats


# --- comparison ----------------------------------------------------------------------------------


@dataclass
class ParityCheck:
    column: str | None
    stat: str
    expected: Value
    actual: Value
    rule: str
    ok: bool
    severity: Literal["error", "warning"] = "error"


@dataclass
class ParityResult:
    checks: list[ParityCheck]

    @property
    def errors(self) -> list[ParityCheck]:
        return [c for c in self.checks if not c.ok and c.severity == "error"]

    @property
    def warnings(self) -> list[ParityCheck]:
        return [c for c in self.checks if not c.ok and c.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": len(self.checks),
            "errors": [asdict(c) for c in self.errors],
            "warnings": [asdict(c) for c in self.warnings],
        }


def _floats_close(a: Value, b: Value, rel_tol: float) -> bool:
    if a is None or b is None:
        return a is b
    x, y = float(a), float(b)
    if math.isnan(x) or math.isnan(y):
        return math.isnan(x) and math.isnan(y)
    return math.isclose(x, y, rel_tol=rel_tol, abs_tol=rel_tol)


def _decimals_equal(a: Value, b: Value) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a) == str(b)


def _rule(stat: str, kind: ParityKind, collation: str | None) -> tuple[str, Literal["error", "warning"]]:
    advisory = kind is ParityKind.TEXT and not is_binary_collation(collation)
    if stat in {"rows", "count", "nulls", "true_count"}:
        return "exact", "error"
    if stat == "distinct":
        return "exact", "warning" if advisory else "error"
    if kind in {ParityKind.FLOAT, ParityKind.FLOAT32}:
        return ("float32-tolerance" if kind is ParityKind.FLOAT32 else "float-tolerance"), "error"
    if kind in {ParityKind.INTEGER, ParityKind.DECIMAL}:
        return "exact-decimal", "error"
    if kind in _TIME_KINDS:
        return "normalised-timestamp", "error"
    if kind is ParityKind.WKT:
        return "normalised-wkt", "error"
    return "exact", "warning" if advisory else "error"


def compare_stats(
    expected: TableStats,
    actual: TableStats,
    columns: Iterable[ParityColumn],
    *,
    float_rel_tol: float = FLOAT_REL_TOL,
    float32_rel_tol: float = FLOAT32_REL_TOL,
) -> ParityResult:
    """Compare Hyper-side (``expected``) with Athena-side (``actual``) statistics."""
    checks = [ParityCheck(None, "rows", expected.rows, actual.rows, "exact", expected.rows == actual.rows)]
    for col in columns:
        kind = ParityKind(col.parity)
        want = expected.columns.get(col.glue_name, {})
        got = actual.columns.get(col.glue_name)
        if got is None:
            checks.append(ParityCheck(col.glue_name, "present", True, False, "exact", False))
            continue
        for stat in sorted(want):
            a, b = want[stat], got.get(stat)
            rule, severity = _rule(stat, kind, col.collation)
            if rule == "float-tolerance":
                ok = _floats_close(a, b, float_rel_tol)
            elif rule == "float32-tolerance":
                ok = _floats_close(a, b, float32_rel_tol)
            elif rule == "exact-decimal":
                ok = _decimals_equal(a, b)
            else:
                ok = a == b
            checks.append(ParityCheck(col.glue_name, stat, a, b, rule, ok, severity))
    return ParityResult(checks)


QueryRunner = Callable[[str], Sequence[Any]]


def measure(
    runner: QueryRunner,
    table_sql: str,
    columns: Sequence[ParityColumn],
    dialect: Dialect,
    *,
    timestamp_precision: Literal["us", "ms", "s"] = "us",
) -> TableStats:
    """Run the stats query through ``runner`` (returns the single result row) and parse it."""
    query = stats_query(table_sql, columns, dialect)
    return parse_stats(query, runner(query.sql), columns, timestamp_precision=timestamp_precision)


# --- runners -------------------------------------------------------------------------------------


def duckdb_parquet_runner(parquet_files: Mapping[str, str]) -> QueryRunner:
    """Run stats queries in DuckDB, with each table name a view over its Parquet file.

    DuckDB (a dev dependency) stands in for Athena locally and in tests: same SQL shape, reading
    exactly the Parquet the converter wrote.
    """
    import duckdb

    conn = duckdb.connect()
    conn.execute("SET TimeZone = 'UTC'")
    for table, path in parquet_files.items():
        literal = "'" + str(path).replace("'", "''") + "'"
        conn.execute(f"CREATE VIEW {_q(table)} AS SELECT * FROM read_parquet({literal})")

    def run(sql: str) -> Sequence[Any]:
        row = conn.execute(sql).fetchone()
        if row is None:
            raise RuntimeError("stats query returned no row")
        return row

    return run


class AthenaRunner:
    """Run a stats query on Athena and return its single row as strings (needs AWS; not run in CI).

    ``client`` is a boto3 Athena client; tests pass a fake with the same three methods.
    """

    def __init__(
        self,
        client: Any,
        workgroup: str,
        *,
        poll_seconds: float = 1.0,
        timeout_seconds: float = 900.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._workgroup = workgroup
        self._poll = poll_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep

    def __call__(self, sql: str) -> Sequence[Any]:
        started = self._client.start_query_execution(QueryString=sql, WorkGroup=self._workgroup)
        query_id = started["QueryExecutionId"]
        waited = 0.0
        while True:
            status = self._client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
            state = status["State"]
            if state == "SUCCEEDED":
                break
            if state in {"FAILED", "CANCELLED"}:
                raise RuntimeError(f"Athena query {query_id} {state}: {status.get('StateChangeReason', '')}")
            if waited >= self._timeout:
                raise TimeoutError(f"Athena query {query_id} still {state} after {waited:.0f}s")
            self._sleep(self._poll)
            waited += self._poll
        rows = self._client.get_query_results(QueryExecutionId=query_id)["ResultSet"]["Rows"]
        if len(rows) < 2:
            raise RuntimeError(f"Athena query {query_id} returned no data row")
        return [cell.get("VarCharValue") for cell in rows[1]["Data"]]
