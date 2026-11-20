""".hyper extract → Parquet → Athena Iceberg conversion (BUILD_PLAN P1.9 / P2.8).

Uses Tableau's Hyper API (Apache-2.0, optional extra ``tabfree-migrate[hyper]``; wheels exist for
Linux x86-64 and Windows, so conversion runs in CI and on Fargate). For every table the converter:

1. writes Parquet with Hyper's own ``COPY ... TO ... (FORMAT PARQUET)``; geography is cast to text,
2. records the row count and per-column parity aggregates (COUNT, and SUM for numeric columns)
   so the Athena copy can be verified against the original extract,
3. emits Athena DDL: an external staging table over the Parquet files plus a CTAS into Iceberg.

Telemetry to Tableau is always disabled.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Hyper SQL type (as reported by the catalog) → Athena/Iceberg column type.
_TYPE_MAP = {
    "BOOL": "boolean",
    "SMALL_INT": "smallint",
    "INT": "integer",
    "BIG_INT": "bigint",
    "OID": "bigint",
    "DOUBLE": "double",
    "FLOAT": "double",
    "TEXT": "string",
    "VARCHAR": "string",
    "CHAR": "string",
    "JSON": "string",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP_TZ": "timestamp",
    "TIME": "string",
    "INTERVAL": "string",
    "GEOGRAPHY": "string",
    "BYTES": "binary",
}
_NUMERIC = {"SMALL_INT", "INT", "BIG_INT", "DOUBLE", "FLOAT", "NUMERIC"}
_TEXT_CAST = {"GEOGRAPHY", "TIME", "INTERVAL"}
_IDENT = re.compile(r"[^a-z0-9_]+")


def athena_type(hyper_type: str, precision: int | None = None, scale: int | None = None) -> str:
    """Map a Hyper type name (e.g. ``NUMERIC``, ``TEXT``) to an Athena/Iceberg type."""
    name = hyper_type.upper()
    if name == "NUMERIC":
        if precision and precision <= 38:
            return f"decimal({precision},{scale or 0})"
        return "double"
    return _TYPE_MAP.get(name, "string")


def glue_identifier(name: str, *, prefix: str = "c") -> str:
    """Glue/Athena-safe identifier: lower-case letters, digits and underscores only."""
    ident = _IDENT.sub("_", name.strip().lower()).strip("_") or prefix
    if ident[0].isdigit():
        ident = f"{prefix}_{ident}"
    return ident[:255]


def unique_identifiers(names: list[str]) -> list[str]:
    """Sanitise names and de-duplicate collisions (``Sales`` and ``sales`` → ``sales``, ``sales_2``)."""
    seen: dict[str, int] = {}
    result = []
    for name in names:
        base = glue_identifier(name)
        count = seen.get(base, 0) + 1
        seen[base] = count
        result.append(base if count == 1 else f"{base}_{count}")
    return result


def _quote_hyper(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass
class ColumnInfo:
    name: str
    glue_name: str
    hyper_type: str
    athena_type: str
    nullable: bool


@dataclass
class TableResult:
    schema: str
    table: str
    glue_table: str
    rows: int
    columns: list[ColumnInfo]
    parquet: str
    parity: dict[str, dict[str, float | int | None]] = field(default_factory=dict)


def athena_ddl(
    result: TableResult, *, database: str, staging_location: str, iceberg_location: str
) -> list[str]:
    """Athena statements: external Parquet staging table, then CTAS into an Iceberg table."""
    staging = f"{result.glue_table}__staging"
    cols = ",\n  ".join(f"`{c.glue_name}` {c.athena_type}" for c in result.columns)
    return [
        f"CREATE EXTERNAL TABLE IF NOT EXISTS `{database}`.`{staging}` (\n  {cols}\n)\n"
        f"STORED AS PARQUET\nLOCATION {_quote_sql_string(staging_location)}",
        f"CREATE TABLE `{database}`.`{result.glue_table}`\n"
        f"WITH (table_type = 'ICEBERG', format = 'PARQUET', "
        f"location = {_quote_sql_string(iceberg_location)}, is_external = false)\n"
        f"AS SELECT * FROM `{database}`.`{staging}`",
        f"DROP TABLE `{database}`.`{staging}`",
    ]


def convert_hyper(path: Path, out_dir: Path) -> list[TableResult]:
    """Export every table of a .hyper file to Parquet under ``out_dir``; return table metadata."""
    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[TableResult] = []
    with (
        HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper,
        Connection(hyper.endpoint, str(path), CreateMode.NONE) as conn,
    ):
        table_names = [
            table
            for schema in conn.catalog.get_schema_names()
            for table in conn.catalog.get_table_names(schema)
        ]
        glue_tables = unique_identifiers([t.name.unescaped for t in table_names])
        for table_name, glue_table in zip(table_names, glue_tables, strict=True):
            definition = conn.catalog.get_table_definition(table_name)
            raw_names = [c.name.unescaped for c in definition.columns]
            columns = []
            for col, glue_name in zip(definition.columns, unique_identifiers(raw_names), strict=True):
                tag = str(col.type.tag).split(".")[-1]
                columns.append(
                    ColumnInfo(
                        name=col.name.unescaped,
                        glue_name=glue_name,
                        hyper_type=tag,
                        athena_type=athena_type(tag, col.type.precision, col.type.scale),
                        nullable=str(col.nullability).endswith("NULLABLE")
                        and "NOT" not in str(col.nullability),
                    )
                )
            select = ", ".join(
                (f"CAST({_quote_hyper(c.name)} AS TEXT)" if c.hyper_type in _TEXT_CAST
                 else _quote_hyper(c.name)) + f" AS {_quote_hyper(c.glue_name)}"
                for c in columns
            )  # fmt: skip
            target = out_dir / f"{glue_table}.parquet"
            conn.execute_command(
                f"COPY (SELECT {select} FROM {table_name}) TO "
                f"{_quote_sql_string(str(target))} WITH (FORMAT PARQUET)"
            )
            rows = int(conn.execute_scalar_query(f"SELECT COUNT(*) FROM {table_name}"))
            parity: dict[str, dict[str, float | int | None]] = {}
            for c in columns:
                quoted = _quote_hyper(c.name)
                stats: dict[str, float | int | None] = {
                    "count": int(conn.execute_scalar_query(f"SELECT COUNT({quoted}) FROM {table_name}"))
                }
                if c.hyper_type in _NUMERIC:
                    total = conn.execute_scalar_query(f"SELECT SUM({quoted}) FROM {table_name}")
                    stats["sum"] = None if total is None else float(total)
                parity[c.glue_name] = stats
            results.append(
                TableResult(
                    schema=table_name.schema_name.name.unescaped if table_name.schema_name else "",
                    table=table_name.name.unescaped,
                    glue_table=glue_table,
                    rows=rows,
                    columns=columns,
                    parquet=str(target),
                    parity=parity,
                )
            )
    return results


def write_report(results: list[TableResult], path: Path) -> None:
    path.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")


def report_dicts(results: list[TableResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
