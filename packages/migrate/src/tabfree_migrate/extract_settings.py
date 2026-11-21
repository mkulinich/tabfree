"""Extract settings from the IR become table definitions (BUILD_PLAN P2.8, for P6 refresh).

Tableau bakes an extract's recipe into the data: extract filters drop rows, "aggregate data for
visible dimensions" rolls rows up, and incremental refresh appends rows whose key is above the
current maximum. The converted Iceberg table holds the result; this module records the recipe
so the Phase 6 refresh pipeline can rebuild or extend it without re-reading Tableau:

- :func:`settings_from_ir` reads ``datasources[].extract`` from a parsed workbook or data source.
  The IR is still growing, so every key is optional and unknown keys are kept in ``other``.
- :func:`table_definition` resolves the incremental key to a Glue column and assembles the
  definition stored in the conversion report.
- :func:`glue_parameters` flattens it into ``tabfree.*`` string parameters. Athena accepts only a
  fixed list of Iceberg table properties, so these go on the Glue table (``glue:UpdateTable``
  after the CTAS) and on the external staging table's ``TBLPROPERTIES``.

Filters are recorded verbatim (Tableau's attributes). They already hold in the data and must be
re-applied only on a full rebuild from the original source.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from tabfree_migrate.extract_types import GEOGRAPHY_SRID, ParityKind, is_binary_collation

_KNOWN = {"enabled", "file", "refresh", "incrementalKey", "filters", "aggregated", "raw"}
_BRACKETS = re.compile(r"^\[(.*)\]$")


def _truthy(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


@dataclass
class ExtractSettings:
    datasource: str | None = None
    enabled: bool | None = None
    file: str | None = None
    incremental_key: str | None = None
    incremental: bool = False
    refresh: dict[str, Any] = field(default_factory=dict)
    filters: list[dict[str, Any]] = field(default_factory=list)
    aggregated: bool | None = None
    aggregation: dict[str, Any] = field(default_factory=dict)
    sample: dict[str, Any] = field(default_factory=dict)
    other: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def settings_from_datasource(datasource: Mapping[str, Any]) -> ExtractSettings | None:
    extract = datasource.get("extract")
    if not isinstance(extract, Mapping):
        return None
    refresh = extract.get("refresh")
    refresh = dict(refresh) if isinstance(refresh, Mapping) else {}
    raw = extract.get("raw")
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    key = extract.get("incrementalKey") or refresh.get("increment-key") or raw.get("increment-key")
    filters = extract.get("filters")
    aggregated = _truthy(extract.get("aggregated"))
    aggregation = {k: v for k, v in raw.items() if "aggreg" in k.lower() or "rollup" in k.lower()}
    if aggregated is None and aggregation:
        aggregated = any(_truthy(v) for v in aggregation.values())
    sample = {k: raw[k] for k in ("count", "units", "top-n", "sample") if k in raw}
    name = datasource.get("caption") or datasource.get("name")
    return ExtractSettings(
        datasource=str(name) if name else None,
        enabled=_truthy(extract.get("enabled")),
        file=str(extract["file"]) if extract.get("file") else None,
        incremental_key=str(key) if key else None,
        incremental=bool(key) or bool(_truthy(refresh.get("incremental-updates"))),
        refresh=refresh,
        filters=[dict(f) for f in filters if isinstance(f, Mapping)] if isinstance(filters, list) else [],
        aggregated=aggregated,
        aggregation=aggregation,
        sample=sample,
        other={k: v for k, v in extract.items() if k not in _KNOWN},
    )


def settings_from_ir(ir: Mapping[str, Any]) -> list[ExtractSettings]:
    """Every extract recipe in a workbook or data source IR (missing keys tolerated)."""
    datasources = ir.get("datasources")
    if not isinstance(datasources, list):
        return []
    found = []
    for ds in datasources:
        if isinstance(ds, Mapping) and (settings := settings_from_datasource(ds)) is not None:
            found.append(settings)
    return found


def _basename(path: str) -> str:
    return PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name


def match_settings(candidates: Sequence[ExtractSettings], member: str) -> ExtractSettings | None:
    """The settings whose ``file`` names this package member (by base name), or the only one."""
    target = _basename(member).lower()
    for settings in candidates:
        if settings.file and _basename(settings.file).lower() == target:
            return settings
    with_files = [s for s in candidates if s.file]
    return candidates[0] if len(candidates) == 1 and not with_files else None


def resolve_field(field_ref: str, columns: Sequence[Any]) -> Any | None:
    """Find the column a Tableau field reference (``[Order Date]``) names, case-insensitively."""
    name = field_ref.strip()
    if "].[" in name:  # [datasource].[field]
        name = name.rsplit("].[", 1)[1].rstrip("]")
    match = _BRACKETS.match(name)
    name = (match.group(1) if match else name).strip().lower()
    for column in columns:
        if column.name.lower() == name or column.glue_name == name:
            return column
    return None


def table_definition(
    settings: ExtractSettings | None,
    columns: Sequence[Any],
    *,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """The recipe stored with each converted table (``columns`` are ``hyper.ColumnInfo``)."""
    definition: dict[str, Any] = {"source": dict(source), "settings_found": settings is not None}
    warnings: list[str] = []
    if settings is not None:
        incremental: dict[str, Any] | None = None
        if settings.incremental:
            column = resolve_field(settings.incremental_key, columns) if settings.incremental_key else None
            incremental = {
                "tableau_field": settings.incremental_key,
                "column": column.name if column else None,
                "glue_column": column.glue_name if column else None,
                "strategy": "append rows with key > max(key), then MERGE on Athena (P6)",
            }
            if column is None:
                warnings.append(f"incremental key {settings.incremental_key!r} matches no column")
        definition.update(
            datasource=settings.datasource,
            incremental=incremental,
            filters={"applied_in_data": True, "tableau": settings.filters},
            aggregated=settings.aggregated,
            aggregation=settings.aggregation,
            sample=settings.sample,
            refresh=settings.refresh,
        )
    else:
        warnings.append("no extract settings in the IR: filters, aggregation and incremental key unknown")
    definition["geography"] = {
        c.glue_name: {"encoding": "WKT", "srid": GEOGRAPHY_SRID}
        for c in columns
        if c.parity == ParityKind.WKT.value
    }
    definition["time_zones"] = {
        c.glue_name: "UTC" for c in columns if c.parity == ParityKind.TIMESTAMP_TZ.value
    }
    definition["collations"] = {
        c.glue_name: c.collation for c in columns if not is_binary_collation(c.collation)
    }
    definition["warnings"] = warnings
    return definition


def glue_parameters(definition: Mapping[str, Any]) -> dict[str, str]:
    """Flatten a table definition into Glue table parameters (all strings, ``tabfree.`` keys)."""
    params: dict[str, str] = {"tabfree.definition": json.dumps(definition, sort_keys=True, default=str)}
    source = definition.get("source", {})
    for key in ("sha256", "table"):
        if source.get(key):
            params[f"tabfree.source.{key}"] = str(source[key])
    incremental = definition.get("incremental")
    if incremental:
        params["tabfree.extract.incremental_key"] = str(incremental.get("glue_column") or "")
        params["tabfree.extract.incremental_field"] = str(incremental.get("tableau_field") or "")
    if definition.get("settings_found"):
        aggregated = definition.get("aggregated")
        params["tabfree.extract.aggregated"] = "unknown" if aggregated is None else str(aggregated).lower()
        params["tabfree.extract.filters"] = json.dumps(definition["filters"]["tableau"], sort_keys=True)
    for column, geo in definition.get("geography", {}).items():
        params[f"tabfree.geo.{column}.encoding"] = geo["encoding"]
        params[f"tabfree.geo.{column}.srid"] = str(geo["srid"])
    for column, zone in definition.get("time_zones", {}).items():
        params[f"tabfree.tz.{column}"] = zone
    for column, collation in definition.get("collations", {}).items():
        params[f"tabfree.collation.{column}"] = str(collation)
    return params
