"""Rollups (BUILD_PLAN P6.10): queries that fit a pre-aggregated table read it instead of the base data.

A model declares rollups::

    rollups:
      - id: sales_by_region_day
        table: orders_rollup_region_day
        dimensions: [Region, Category, Order Date]
        measures: [Sales, Profit, Quantity]

**Building** (a refresh job, mode ``rollup``): the rollup's semantic query is compiled against the
model *without* its row-level security (the table holds everyone's data) and written with an Iceberg
CTAS on Athena or ``CREATE TABLE AS`` on Redshift, one column per field.

**Routing** (the query service): a query uses the rollup only when that cannot change its answer:

- every dimension, filter and sort is on a rollup dimension (date truncations and parts are fine:
  the rollup keeps the raw value), with no free expressions;
- every measure is a rollup measure whose aggregation re-aggregates exactly (sum, count as a sum,
  min, max), or a calculated measure built only from those (``SUM([Profit]) / SUM([Sales])``);
- no level-of-detail expressions, sets, blends or Top N by an expression;
- the model's RLS policies only use rollup dimensions, and are applied to the rollup too. A rollup
  that cannot carry a policy is never used, so a rollup can never widen what someone sees.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

REAGGREGATE = {"sum": "sum", "count": "sum", "min": "min", "max": "max"}
_REF = re.compile(r"\[([^\]]+)\]")
_NON_ADDITIVE = re.compile(
    r"\b(COUNTD|AVG|MEDIAN|PERCENTILE|STDEV|STDEVP|VAR|VARP|ATTR|INDEX|RANK|WINDOW_\w+|RUNNING_\w+|LOOKUP|TOTAL|FIXED|INCLUDE|EXCLUDE)\b",
    re.I,
)


def column_name(field: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_") or "c"
    return value if value[0].isalpha() else f"c_{value}"


def _fields(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(f["name"]): f for f in doc.get("fields", [])}


def _strings(value: Any) -> set[str]:
    """Every string inside a policy (field names in entitlement mappings, attribute names...)."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return set().union(*(_strings(v) for v in value.values())) if value else set()
    if isinstance(value, list | tuple):
        return set().union(*(_strings(v) for v in value)) if value else set()
    return set()


def _refs(formula: str) -> set[str]:
    return {r for r in _REF.findall(formula) if not r.startswith("Parameters")}


def derived_model(doc: Mapping[str, Any], rollup: Mapping[str, Any]) -> dict[str, Any] | None:
    """The model of the rollup table, or None when the rollup cannot serve this model safely."""
    fields = _fields(doc)
    dims, measures = list(rollup["dimensions"]), list(rollup["measures"])
    if any(name not in fields for name in dims + measures):
        return None
    columns = []
    out_fields: list[dict[str, Any]] = []
    for name in dims:
        f = fields[name]
        if f.get("formula") and f.get("role") == "measure":
            return None
        columns.append({"name": column_name(name), "type": f.get("type", "string")})
        out_fields.append(
            {k: v for k, v in f.items() if k not in ("table", "column", "formula", "defaultAggregation")}
            | {"table": rollup["id"], "column": column_name(name), "role": "dimension"}
        )
    for name in measures:
        f = fields[name]
        aggregation = REAGGREGATE.get(str(f.get("defaultAggregation", "sum")))
        if f.get("formula") or aggregation is None:
            return None  # a rollup measure is a stored, re-aggregatable column
        columns.append({"name": column_name(name), "type": f.get("type", "float")})
        out_fields.append(
            {k: v for k, v in f.items() if k not in ("table", "column", "formula")}
            | {
                "table": rollup["id"],
                "column": column_name(name),
                "role": "measure",
                "defaultAggregation": aggregation,
            }
        )
    available = set(dims) | set(measures)
    for name, f in fields.items():
        formula = f.get("formula")
        if name in available or not formula or f.get("role") != "measure":
            continue
        if (
            _refs(str(formula)) <= set(measures)
            and not _NON_ADDITIVE.search(str(formula))
            and "{" not in str(formula)
        ):
            out_fields.append({k: v for k, v in f.items() if k not in ("table", "column")})
    policies = []
    for policy in doc.get("rls", []) or []:
        used = {name for name in _strings(policy) | set(_REF.findall(repr(policy))) if name in fields}
        if not used <= set(dims):
            return None  # the policy needs a column the rollup does not have
        policies.append(policy)
    source: dict[str, Any] = {"table": rollup["table"]}
    if rollup.get("database"):
        source["database"] = rollup["database"]
    derived: dict[str, Any] = {
        "schema": doc.get("schema", "tabfree/model@0"),
        "id": f"{doc['id']}__rollup__{rollup['id']}",
        "title": f"{doc.get('title', doc['id'])} ({rollup['id']})",
        "connection": doc["connection"],
        "tables": [{"id": rollup["id"], "source": source, "columns": columns}],
        "fields": out_fields,
    }
    for key in ("settings", "parameters"):
        if doc.get(key):
            derived[key] = doc[key]
    if policies:
        derived["rls"] = policies
    return derived


def _names(items: Iterable[Any]) -> tuple[set[str], bool]:
    """Field names used by dimensions or measures, and whether everything was a plain reference."""
    out: set[str] = set()
    plain = True
    for item in items:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, Mapping) and "field" in item:
            out.add(str(item["field"]))
        else:
            plain = False
    return out, plain


def fits(query: Mapping[str, Any], filters: Sequence[Mapping[str, Any]], derived: Mapping[str, Any]) -> bool:
    if query.get("blend") or query.get("densify"):
        return False
    fields = _fields(derived)
    dimension_names = {n for n, f in fields.items() if f.get("role") == "dimension"}
    dims, plain_dims = _names(query.get("dimensions", []))
    if not plain_dims or not dims <= dimension_names:
        return False
    measures, plain_measures = _names(query.get("measures", []))
    if not plain_measures or not measures <= set(fields):
        return False
    for item in query.get("measures", []):
        if isinstance(item, Mapping):
            agg = item.get("agg")
            base = fields[str(item["field"])]
            if agg is not None and (
                base.get("formula") or REAGGREGATE.get(str(agg)) != base.get("defaultAggregation")
            ):
                return False
    for flt in list(query.get("where", [])) + list(filters):
        if "field" not in flt or str(flt["field"]) not in dimension_names:
            return False
        if flt.get("kind") in ("condition", "top_n"):
            return False
    for sort in query.get("sort", []) or []:
        by = str(sort.get("by", ""))
        outputs = {
            str(i.get("as") or i.get("field")) if isinstance(i, Mapping) else str(i)
            for i in [*query.get("dimensions", []), *query.get("measures", [])]
        }
        if sort.get("expr") or (by not in outputs and by not in fields):
            return False
    return True


def choose(
    doc: Mapping[str, Any], query: Mapping[str, Any], filters: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """The derived model of the smallest rollup the query fits, or None to read the base tables."""
    candidates = []
    for rollup in doc.get("rollups", []) or []:
        derived = derived_model(doc, rollup)
        if derived is not None and fits(query, filters, derived):
            candidates.append((len(rollup["dimensions"]), derived))
    return min(candidates, key=lambda c: c[0])[1] if candidates else None


def build_query(doc: Mapping[str, Any], rollup: Mapping[str, Any]) -> dict[str, Any]:
    """The semantic query that computes a rollup (one output column per field, named for SQL)."""
    fields = _fields(doc)
    return {
        "dimensions": [{"field": d, "as": column_name(d)} for d in rollup["dimensions"]],
        "measures": [
            {"field": m, "agg": str(fields[m].get("defaultAggregation", "sum")), "as": column_name(m)}
            for m in rollup["measures"]
        ],
    }


def without_rls(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The model as the rollup builder sees it: all rows (policies are applied when reading)."""
    return {k: v for k, v in doc.items() if k != "rls"}
