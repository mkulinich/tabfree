""".hyper extract → Parquet → Athena Iceberg conversion (BUILD_PLAN P1.9 / P2.8).

Uses Tableau's Hyper API (Apache-2.0, optional extra ``tabfree-migrate[hyper]``; wheels exist for
Linux x86-64 and Windows, so conversion runs in CI and on Fargate). For every table the converter:

1. maps each column with :mod:`tabfree_migrate.extract_types` (geography, time and interval are
   cast to text; geography becomes WKT, SRID 4326),
2. writes Parquet with Hyper's own ``COPY ... TO ... (FORMAT PARQUET)``,
3. computes per-column parity statistics in Hyper (:mod:`tabfree_migrate.parity`) so the Athena
   copy can be verified against the original extract,
4. and :func:`athena_ddl` emits the Athena DDL: an external staging table over the Parquet files,
   a CTAS into Iceberg, and the staging drop.

Telemetry to Tableau is always disabled. Hyper writes its log into ``log_dir`` (default: the
output directory), so the converter also runs with a read-only root file system.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tabfree_migrate.extract_types import map_type
from tabfree_migrate.parity import StatsQuery, TableStats, athena_table, parse_stats, stats_query

_IDENT = re.compile(r"[^a-z0-9_]+")


def athena_type(
    hyper_type: str, precision: int | None = None, scale: int | None = None, collation: str | None = None
) -> str:
    """Map a Hyper type name (e.g. ``NUMERIC``, ``TEXT``) to an Athena/Iceberg type."""
    return map_type(hyper_type, precision, scale, collation).athena


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
    export_as_text: bool = False
    parity: str = ""
    collation: str | None = None
    scale: int | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.parity:
            mapped = map_type(self.hyper_type, None, self.scale, self.collation)
            self.parity = mapped.parity.value
            self.export_as_text = self.export_as_text or mapped.export_as_text

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ColumnInfo:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class TableResult:
    schema: str
    table: str
    glue_table: str
    rows: int
    columns: list[ColumnInfo]
    parquet: str
    #: Per-column statistics computed in Hyper (``parity.TableStats.to_dict()``).
    stats: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TableResult:
        return cls(
            schema=str(data.get("schema", "")),
            table=str(data["table"]),
            glue_table=str(data["glue_table"]),
            rows=int(data["rows"]),
            columns=[ColumnInfo.from_dict(c) for c in data["columns"]],
            parquet=str(data.get("parquet", "")),
            stats=dict(data.get("stats", {})),
        )

    def hyper_stats(self) -> TableStats:
        return TableStats.from_dict(self.stats)


def _properties(properties: Mapping[str, str]) -> str:
    pairs = ",\n  ".join(f"{_quote_sql_string(k)} = {_quote_sql_string(v)}" for k, v in properties.items())
    return f"\nTBLPROPERTIES (\n  {pairs}\n)"


def _column_list(result: TableResult) -> str:
    return ",\n  ".join(f"`{c.glue_name}` {c.athena_type}" for c in result.columns)


def athena_ddl(
    result: TableResult,
    *,
    database: str,
    staging_location: str,
    iceberg_location: str,
    table_properties: Mapping[str, str] | None = None,
) -> list[str]:
    """Athena statements: external Parquet staging table, then CTAS into an Iceberg table, then drop.

    ``table_properties`` (the extract recipe, ``tabfree.*`` keys) go on the staging table; Athena
    allows only a fixed set of Iceberg properties, so the same keys are also applied to the Iceberg
    table as Glue parameters (``glue:UpdateTable``) after the CTAS.
    """
    staging = f"{result.glue_table}__staging"
    props = _properties(table_properties) if table_properties else ""
    return [
        f"CREATE EXTERNAL TABLE IF NOT EXISTS `{database}`.`{staging}` (\n  {_column_list(result)}\n)\n"
        f"STORED AS PARQUET\nLOCATION {_quote_sql_string(staging_location)}{props}",
        f"CREATE TABLE `{database}`.`{result.glue_table}`\n"
        f"WITH (table_type = 'ICEBERG', format = 'PARQUET', "
        f"location = {_quote_sql_string(iceberg_location)}, is_external = false)\n"
        f"AS SELECT * FROM `{database}`.`{staging}`",
        f"DROP TABLE `{database}`.`{staging}`",
    ]


def iceberg_table_ddl(result: TableResult, *, database: str, location: str) -> str:
    """Explicit Iceberg ``CREATE TABLE`` (no data): what a Phase 6 full rebuild starts from."""
    return (
        f"CREATE TABLE `{database}`.`{result.glue_table}` (\n  {_column_list(result)}\n)\n"
        f"LOCATION {_quote_sql_string(location)}\n"
        "TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')"
    )


def athena_parity_query(result: TableResult, *, database: str) -> StatsQuery:
    """The per-column statistics query for the converted table, in Athena SQL."""
    return stats_query(athena_table(database, result.glue_table), result.columns, "athena")


def convert_hyper(
    path: Path, out_dir: Path, *, table_prefix: str = "", log_dir: Path | None = None
) -> list[TableResult]:
    """Export every table of a .hyper file to Parquet under ``out_dir``; return table metadata."""
    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry

    out_dir.mkdir(parents=True, exist_ok=True)
    parameters = {"log_dir": str(log_dir or out_dir)}
    results: list[TableResult] = []
    with (
        HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters=parameters) as hyper,
        Connection(hyper.endpoint, str(path), CreateMode.NONE) as conn,
    ):
        table_names = [
            table
            for schema in conn.catalog.get_schema_names()
            for table in conn.catalog.get_table_names(schema)
        ]
        prefix = f"{table_prefix}__" if table_prefix else ""
        glue_tables = unique_identifiers([prefix + t.name.unescaped for t in table_names])
        for table_name, glue_table in zip(table_names, glue_tables, strict=True):
            definition = conn.catalog.get_table_definition(table_name)
            raw_names = [c.name.unescaped for c in definition.columns]
            columns = []
            for col, glue_name in zip(definition.columns, unique_identifiers(raw_names), strict=True):
                tag = str(col.type.tag).split(".")[-1]
                collation = getattr(col, "collation", None)
                collation = str(collation) if collation else None
                mapped = map_type(tag, col.type.precision, col.type.scale, collation)
                columns.append(
                    ColumnInfo(
                        name=col.name.unescaped,
                        glue_name=glue_name,
                        hyper_type=tag,
                        athena_type=mapped.athena,
                        nullable="NOT" not in str(col.nullability).upper(),
                        export_as_text=mapped.export_as_text,
                        parity=mapped.parity.value,
                        collation=collation,
                        scale=mapped.scale,
                        notes=mapped.notes,
                    )
                )
            select = ", ".join(
                (f"CAST({_quote_hyper(c.name)} AS TEXT)" if c.export_as_text
                 else _quote_hyper(c.name)) + f" AS {_quote_hyper(c.glue_name)}"
                for c in columns
            )  # fmt: skip
            target = out_dir / f"{glue_table}.parquet"
            conn.execute_command(
                f"COPY (SELECT {select} FROM {table_name}) TO "
                f"{_quote_sql_string(str(target))} WITH (FORMAT PARQUET)"
            )
            query = stats_query(str(table_name), columns, "hyper")
            row = conn.execute_list_query(query.sql)[0]
            stats = parse_stats(query, row, columns)
            results.append(
                TableResult(
                    schema=table_name.schema_name.name.unescaped if table_name.schema_name else "",
                    table=table_name.name.unescaped,
                    glue_table=glue_table,
                    rows=stats.rows,
                    columns=columns,
                    parquet=str(target),
                    stats=stats.to_dict(),
                )
            )
    return results


def write_report(results: list[TableResult], path: Path) -> None:
    path.write_text(json.dumps(report_dicts(results), indent=2, default=str), encoding="utf-8")


def report_dicts(results: list[TableResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
