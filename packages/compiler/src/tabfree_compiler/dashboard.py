"""Bind a dashboard's runtime state to its named queries (the governed half of ADR-002).

The browser sends only names and values: which query, the current filter values, parameters and
mark selections. This module maps them onto the manifest's declarations, so a viewer can narrow
data only in the ways the dashboard author declared, and only declared actions turn selections
into filters. Row-level security is applied later by the compiler from the viewer's session.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tabfree_compiler.model import CompileError

MAX_MEMBERS = 10_000
MAX_DOMAIN = 10_000


@dataclass(frozen=True)
class BoundQuery:
    model: str
    query: Mapping[str, Any]
    filters: tuple[Mapping[str, Any], ...]
    parameters: Mapping[str, Any]


def _applies(spec: Mapping[str, Any], query: str) -> bool:
    target = spec.get("appliesTo", "all")
    return target == "all" or query in target


def _target(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"field": spec["field"]} if "field" in spec else {"expr": spec["expr"]}


def _filter_from_value(name: str, spec: Mapping[str, Any], value: Any) -> dict[str, Any] | None:
    """A queryFilter for one dashboard filter's current value; None means no restriction."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CompileError(f"filter {name!r}: value must be an object")
    kind = spec["kind"]
    out: dict[str, Any] = {**_target(spec), "kind": kind}
    for flag in ("context", "exclude", "includeNulls"):
        if flag in spec:
            out[flag] = spec[flag]
    if kind == "categorical" and set(value) == {"members"}:
        members = value["members"]
        if not isinstance(members, list) or len(members) > MAX_MEMBERS:
            raise CompileError(f"filter {name!r}: members must be a list of at most {MAX_MEMBERS}")
        if not spec.get("multi", True) and len(members) > 1:
            raise CompileError(f"filter {name!r} takes a single member")
        out["members"] = members
    elif kind in ("range", "relative_date") and value and set(value) <= {"min", "max"}:
        out["kind"] = "range"
        out.update({k: value[k] for k in ("min", "max") if k in value})
    elif kind == "relative_date" and set(value) == {"relative"}:
        out["relative"] = dict(value["relative"])
    elif kind == "wildcard" and set(value) == {"wildcard"}:
        out["wildcard"] = dict(value["wildcard"])
    else:
        raise CompileError(f"filter {name!r}: value does not fit a {kind} filter")
    return out


def _coerce_parameter(name: str, spec: Mapping[str, Any], value: Any) -> Any:
    type_ = spec.get("type")
    if value is None:
        return None
    if type_ == "integer":
        if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
            raise CompileError(f"parameter {name!r} must be an integer")
        value = int(value)
    elif type_ == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CompileError(f"parameter {name!r} must be a number")
        value = float(value)
    elif type_ == "boolean":
        if not isinstance(value, bool):
            raise CompileError(f"parameter {name!r} must be true or false")
    elif type_ in ("date", "datetime"):
        if not isinstance(value, str):
            raise CompileError(f"parameter {name!r} must be an ISO date")
        try:
            (dt.date if type_ == "date" else dt.datetime).fromisoformat(value)
        except ValueError:
            raise CompileError(f"parameter {name!r} must be an ISO date") from None
    elif not isinstance(value, str):
        raise CompileError(f"parameter {name!r} must be text")
    allowed = spec.get("allowed")
    if isinstance(allowed, Mapping):
        if "list" in allowed and value not in allowed["list"]:
            raise CompileError(f"parameter {name!r}: value is not one of the allowed values")
        bounds = allowed.get("range") or {}
        if isinstance(value, int | float):
            if isinstance(bounds.get("min"), int | float) and value < bounds["min"]:
                raise CompileError(f"parameter {name!r} is below its minimum")
            if isinstance(bounds.get("max"), int | float) and value > bounds["max"]:
                raise CompileError(f"parameter {name!r} is above its maximum")
    return value


def _field_mapping(action: Mapping[str, Any]) -> dict[str, str] | None:
    fields = action.get("fields")
    if not fields:
        return None
    mapping: dict[str, str] = {}
    for f in fields:
        if isinstance(f, str):
            mapping[f] = f
        else:
            mapping[f["source"]] = f["target"]
    return mapping


def _action_filters(
    manifest: Mapping[str, Any], query: str, selections: Mapping[str, Sequence[Mapping[str, Any]]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action in manifest.get("actions", []):
        if action.get("type") != "filter":
            continue
        targets = action.get("targets", [])
        if targets != "all" and query not in targets:
            continue
        sources = action.get("source", [])
        active = [s for s in selections if (sources == "all" or s in sources) and s != query]
        mapping = _field_mapping(action)
        if not active:
            if action.get("clear") == "exclude_all" and mapping:
                out.extend({"field": t, "kind": "categorical", "members": []} for t in mapping.values())
            continue
        for source in active:
            tuples = list(selections[source])[:MAX_MEMBERS]
            keys = mapping or {k: k for t in tuples for k in t}
            # One categorical filter per field; combinations of several fields over-select (P2.5).
            for source_field, target_field in keys.items():
                members = list(dict.fromkeys(t[source_field] for t in tuples if source_field in t))
                out.append({"field": target_field, "kind": "categorical", "members": members})
    return out


def _model_for(manifest: Mapping[str, Any], query_spec: Mapping[str, Any]) -> str:
    models = manifest.get("models", {})
    alias = query_spec.get("model")
    if alias is None:
        if len(models) != 1:
            raise CompileError("the query must name its model when a dashboard has several")
        return str(next(iter(models.values())))
    if alias not in models:
        raise CompileError(f"unknown model alias {alias!r}")
    return str(models[alias])


def _parameters(manifest: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    declared = manifest.get("parameters", {})
    values = state.get("parameters", {}) or {}
    out = {}
    for name, value in values.items():
        if name not in declared:
            raise CompileError(f"unknown parameter {name!r}")
        out[name] = _coerce_parameter(name, declared[name], value)
    return out


def bind_query(
    manifest: Mapping[str, Any],
    query: str,
    state: Mapping[str, Any],
    selections: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> BoundQuery:
    """Everything the compiler needs to run ``query`` for the given dashboard state."""
    queries = manifest.get("queries", {})
    if query not in queries:
        raise CompileError(f"unknown query {query!r}")
    spec = queries[query]
    declared = manifest.get("filters", {})
    filters: list[dict[str, Any]] = []
    for name, value in (state.get("filters", {}) or {}).items():
        if name not in declared:
            raise CompileError(f"unknown filter {name!r}")
        if _applies(declared[name], query):
            bound = _filter_from_value(name, declared[name], value)
            if bound is not None:
                filters.append(bound)
    for source in selections or {}:
        if source not in queries:
            raise CompileError(f"unknown selection source {source!r}")
    filters += _action_filters(manifest, query, selections or {})
    return BoundQuery(_model_for(manifest, spec), spec, tuple(filters), _parameters(manifest, state))


def bind_domain(manifest: Mapping[str, Any], filter_name: str, state: Mapping[str, Any]) -> BoundQuery:
    """The distinct members a filter control offers.

    With ``onlyRelevantValues`` the other filters (that share a query with this one) narrow the list,
    as in Tableau's cascading filters.
    """
    declared = manifest.get("filters", {})
    if filter_name not in declared:
        raise CompileError(f"unknown filter {filter_name!r}")
    spec = declared[filter_name]
    target = spec.get("field")
    if target is None:
        dimension: Any = {"expr": spec["expr"], "as": "value"}
        name = "value"
    else:
        dimension, name = target, target
    query = {"dimensions": [dimension], "sort": [{"by": name, "dir": "asc"}], "limit": MAX_DOMAIN}
    filters: list[dict[str, Any]] = []
    if spec.get("onlyRelevantValues"):
        for other, value in (state.get("filters", {}) or {}).items():
            if other != filter_name and other in declared:
                bound = _filter_from_value(other, declared[other], value)
                if bound is not None:
                    filters.append(bound)
    models = manifest.get("models", {})
    model = spec.get("model")
    model_id = str(models[model]) if model in models else _model_for(manifest, {})
    return BoundQuery(model_id, query, tuple(filters), _parameters(manifest, state))
