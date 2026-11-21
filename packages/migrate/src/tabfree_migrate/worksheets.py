"""Worksheet → TabFree query (BUILD_PLAN P2.5: grain from shelves and encodings).

A Tableau worksheet's level of detail is the set of discrete and continuous dimension pills on Rows,
Columns and the Marks card (color, size, detail, tooltip, label, path...); aggregated pills are its
measures. This module turns a worksheet's IR (``tabfree/tableau-ir@1``) into a manifest ``query``
against the model that ``modelgen.generate_model`` produced for the worksheet's primary data source,
so a converted view can be run and compared with Tableau's own CSV (``tabfree_compiler.parity``).

What cannot be expressed is reported, never silently dropped: table calculations on pills, fields
from secondary (blended) data sources, and filter shapes the query schema has no form for.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from tabfree_migrate.modelgen import (
    GenerationReport,
    _is_aggregate_formula,
    rewrite_formula,
    tableau_literal,
    unbracket,
)

#: Discrete date parts (blue pills) and truncations (green pills), by Tableau derivation code.
DATE_PARTS = {
    "yr": "year",
    "qr": "quarter",
    "mn": "month",
    "wk": "week",
    "wd": "weekday",
    "dy": "day",
    "hr": "hour",
    "mi": "minute",
    "sc": "second",
    "iyr": "iso_year",
    "iqr": "iso_quarter",
    "iwk": "iso_week",
    "iwd": "iso_weekday",
}
DATE_TRUNCS = {
    "tyr": "year",
    "tqr": "quarter",
    "tmn": "month",
    "twk": "week",
    "tdy": "day",
    "thr": "hour",
    "tmi": "minute",
    "tsc": "second",
    "tiyr": "iso_year",
    "tiqr": "iso_quarter",
    "tiwk": "iso_week",
    "my": "month",  # discrete "Month / Year"
    "md": "day",  # discrete "Month / Day / Year"
    "mdy": "day",
}
AGGREGATIONS = {
    "sum": "sum",
    "avg": "avg",
    "min": "min",
    "max": "max",
    "cnt": "count",
    "ctd": "countd",
    "med": "median",
    "attr": "attr",
    "stdev": "stdev",
    "stdevp": "stdevp",
    "var": "var",
    "varp": "varp",
}
#: Marks-card encodings that add to the level of detail (geometry is a dimension too).
ENCODINGS = (
    "color",
    "size",
    "shape",
    "text",
    "label",
    "detail",
    "lod",
    "tooltip",
    "path",
    "wedgeSize",
    "geometry",
)
_DATE_FUNCTION = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "day": "DAY",
}
# "[ds].[derivation:Name:kind]" (a pill instance) or "[ds].[Name]".
_INSTANCE = re.compile(
    r"^\[(?P<ds>(?:[^\]]|\]\])+)\]\.\[(?:(?P<deriv>[\w-]+):)?(?P<name>.+?)(?::(?P<kind>[nqo]k))?\]$"
)


@dataclass
class WorksheetReport:
    unsupported: list[str] = field(default_factory=list)  # parts of the view not converted
    notes: list[str] = field(default_factory=list)  # assumptions the reviewer should check

    @property
    def ok(self) -> bool:
        return not self.unsupported


@dataclass(frozen=True)
class _Pill:
    datasource: str
    derivation: str
    local: str  # "[Calculation_1]"
    kind: str


def _pill(ref: Mapping[str, Any] | str) -> _Pill | None:
    if isinstance(ref, Mapping):
        if not ref.get("field"):
            return None
        return _Pill(
            str(ref.get("datasource", "")),
            str(ref.get("derivation") or "none").lower(),
            str(ref["field"]),
            str(ref.get("kind") or ""),
        )
    match = _INSTANCE.match(ref.strip().strip('"'))
    if not match:
        return None
    return _Pill(
        match["ds"].replace("]]", "]"),
        (match["deriv"] or "none").lower(),
        "[" + match["name"] + "]",
        match["kind"] or "",
    )


def _shelf_pills(tree: Mapping[str, Any] | None) -> Iterator[Mapping[str, Any]]:
    if not tree:
        return
    if tree.get("op") == "field" and "ref" in tree:
        yield tree["ref"]
    for arg in tree.get("args", []) or []:
        yield from _shelf_pills(arg)


class _Converter:
    def __init__(self, worksheet: Mapping[str, Any], primary: str, model: GenerationReport) -> None:
        self.ws = worksheet
        self.primary = primary
        self.names = model.names
        self.params = model.params
        self.report = WorksheetReport()
        self.dimensions: list[Any] = []
        self.measures: list[Any] = []
        self.outputs: dict[_Pill, str] = {}
        self.measure_names = False

    # -- names --------------------------------------------------------------------------------------
    def field_name(self, pill: _Pill) -> str | None:
        if pill.local in ("[:Measure Names]", "[Multiple Values]"):
            return None
        name = self.names.get(pill.local)
        if name is None and not pill.local.startswith("[Calculation_"):
            name = unbracket(pill.local)
        return name

    def rewrite(self, formula: str) -> str:
        return rewrite_formula(formula, self.names, self.params)

    def target(self, pill: _Pill) -> dict[str, Any] | None:
        """A filter target: the field, or an expression for date parts and aggregates."""
        name = self.field_name(pill)
        if name is None:
            return None
        ref = "[" + name.replace("]", "]]") + "]"
        if pill.derivation in DATE_PARTS:
            part = DATE_PARTS[pill.derivation]
            fn = _DATE_FUNCTION.get(part)
            return {"expr": f"{fn}({ref})" if fn else f"DATEPART('{part}', {ref})"}
        if pill.derivation in DATE_TRUNCS:
            return {"expr": f"DATETRUNC('{DATE_TRUNCS[pill.derivation]}', {ref})"}
        if pill.derivation in AGGREGATIONS:
            return {"expr": f"{pill.derivation.upper() if pill.derivation != 'ctd' else 'COUNTD'}({ref})"}
        return {"field": name}

    def aggregate_expr(self, pill: _Pill) -> str | None:
        name = self.field_name(pill)
        if name is None:
            return None
        ref = "[" + name.replace("]", "]]") + "]"
        if pill.derivation == "usr":
            return ref
        agg = AGGREGATIONS.get(pill.derivation)
        return f"{agg.upper()}({ref})" if agg else None

    # -- shelves and encodings ----------------------------------------------------------------------
    def add(self, ref: Mapping[str, Any], where: str) -> None:
        pill = _pill(ref)
        if pill is None:
            return
        if pill.local == "[:Measure Names]":
            self.measure_names = True
            return
        if pill.local == "[Multiple Values]" or pill in self.outputs:
            return
        if pill.local.startswith("[__tableau_internal_object_id__]"):
            # "Count of <table>" in the object model: rows of the logical table.
            alias = "Number of Records"
            if alias not in self.outputs.values():
                self.measures.append({"expr": "COUNT(1)", "as": alias})
                self.outputs[pill] = alias
                self.report.notes.append(f"{where}: table count converted to COUNT(1) over the joined rows")
            return
        if pill.local in ("[Latitude (generated)]", "[Longitude (generated)]", "[Geometry (generated)]"):
            self.report.unsupported.append(f"{where}: {pill.local} is geocoded by Tableau (map layer, P4)")
            return
        if pill.datasource and pill.datasource not in (self.primary, "Parameters"):
            self.report.unsupported.append(
                f"{where}: {pill.local} comes from secondary data source {pill.datasource!r} (blend)"
            )
            return
        if pill.datasource == "Parameters":
            self.report.notes.append(f"{where}: parameter {pill.local} on the view is a constant")
            return
        name = self.field_name(pill)
        if name is None:
            self.report.unsupported.append(f"{where}: unknown field {pill.local}")
            return
        d = pill.derivation
        if d in DATE_PARTS:
            alias = f"{DATE_PARTS[d].upper()}({name})"
            self.dimensions.append({"field": name, "datePart": DATE_PARTS[d], "as": alias})
        elif d in DATE_TRUNCS:
            alias = f"{DATE_TRUNCS[d].upper()}_TRUNC({name})"
            self.dimensions.append({"field": name, "dateTrunc": DATE_TRUNCS[d], "as": alias})
        elif d in ("none", "io"):
            alias = name
            self.dimensions.append(name)
        elif d == "usr":
            alias = name
            self.measures.append(name)
        elif d in AGGREGATIONS:
            agg = AGGREGATIONS[d]
            alias = f"{agg.upper()}({name})"
            self.measures.append({"field": name, "agg": agg, "as": alias})
        else:
            self.report.unsupported.append(
                f"{where}: {pill.local} uses derivation {d!r} (a quick table calculation or bin); "
                "add it as a tableCalc"
            )
            return
        if alias in self.outputs.values():  # e.g. the same field on two shelves
            (self.measures if d in AGGREGATIONS or d == "usr" else self.dimensions).pop()
        self.outputs[pill] = alias

    def shelves(self) -> None:
        for shelf in ("rows", "cols"):
            for ref in _shelf_pills((self.ws.get(shelf) or {}).get("tree")):
                self.add(ref, shelf)
        for pane in self.ws.get("panes", []) or []:
            for encoding, refs in (pane.get("encodings") or {}).items():
                if encoding not in ENCODINGS:
                    self.report.notes.append(f"encoding {encoding!r} ignored")
                    continue
                for ref in refs:
                    self.add(ref, encoding)

    # -- filters ---------------------------------------------------------------------------------------
    def filters(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for flt in self.ws.get("filters", []) or []:
            pill = _pill(flt.get("ref") or str(flt.get("field", "")))
            if pill is None:
                continue
            if pill.local == "[:Measure Names]":
                self.measure_name_filter(flt)
                continue
            if pill.datasource and pill.datasource != self.primary:
                self.report.unsupported.append(f"filter on {pill.local} from {pill.datasource!r} (blend)")
                continue
            converted = self.filter(flt, pill)
            if converted is not None:
                if flt.get("context"):
                    converted["context"] = True
                out.append(converted)
        return out

    def filter(self, flt: Mapping[str, Any], pill: _Pill) -> dict[str, Any] | None:
        target = self.target(pill)
        label = f"filter on {pill.local}"
        if target is None:
            self.report.unsupported.append(f"{label}: unknown field")
            return None
        kind = flt.get("kind")
        tree = flt.get("tree") or {}
        enumeration = (tree.get("ui") or {}).get("ui-enumeration")
        if kind == "categorical":
            if flt.get("all") or enumeration == "all" or "members" not in flt:
                return None  # "all values": no restriction
            members = [tableau_literal(m) for m in flt["members"]]
            spec: dict[str, Any] = {**target, "kind": "categorical", "members": members}
            if enumeration == "exclusive" or flt.get("exclude"):
                spec["exclude"] = True
            return spec
        if kind == "quantitative":
            spec = {**target, "kind": "range"}
            for bound in ("min", "max"):
                if flt.get(bound) not in (None, ""):
                    value = tableau_literal(flt[bound])
                    if isinstance(value, str):
                        with contextlib.suppress(ValueError):
                            value = float(value)
                    spec[bound] = value
            included = flt.get("includedValues", "in-range")
            if included == "non-null" and "min" not in spec and "max" not in spec:
                if pill.derivation in AGGREGATIONS:
                    self.report.notes.append(
                        f"{label}: 'non-null' on an aggregate dropped (only hides null marks)"
                    )
                    return None
                return {
                    "expr": f"NOT ISNULL({self.ref_expr(target)})",
                    "kind": "categorical",
                    "members": [True],
                }
            if "min" not in spec and "max" not in spec:
                return None
            if included == "in-range-or-null":
                spec["includeNulls"] = True
            return spec
        if kind == "relative-date":
            period = str(flt.get("period", "day"))
            return {
                **target,
                "kind": "relative_date",
                "relative": {
                    "period": period,
                    "from": int(flt.get("firstPeriod", 0)),
                    "to": int(flt.get("lastPeriod", 0)),
                },
            }
        if kind == "top-n":
            top = flt.get("topN") or {}
            by = self.rewrite(str(top.get("by", "")))
            if re.fullmatch(r"\[(?:[^\]]|\]\])+\]", by):
                by = f"SUM({by})"
                self.report.notes.append(f"{label}: Top N by a bare field assumed SUM")
            count = top.get("count")
            n: Any = count
            if count in (None, ""):
                self.report.unsupported.append(f"{label}: Top N with no count")
                return None
            if isinstance(count, str) and count.lower().startswith("[parameters]."):
                n = {"param": self.params.get(count[len("[Parameters].") :], unbracket(count))}
            return {
                **target,
                "kind": "top_n",
                "topN": {"n": n, "by": by, "direction": "bottom" if top.get("end") == "bottom" else "top"},
            }
        if kind == "condition":
            condition = self.rewrite(str(flt.get("condition", "")))
            if not _is_aggregate_formula(condition):
                # A row-level condition (e.g. on the filtered field itself) keeps the rows where it holds.
                return {"expr": condition, "kind": "categorical", "members": [True]}
            return {**target, "kind": "condition", "condition": condition}
        if kind == "wildcard":
            wild = flt.get("wildcard") or {}
            text = wild.get("ui-pattern_text")
            if not text:
                return None
            match = {
                "contains": "contains",
                "startsWith": "starts_with",
                "starts-with": "starts_with",
                "endsWith": "ends_with",
                "ends-with": "ends_with",
                "exact": "exact",
            }.get(str(wild.get("ui-pattern_type", "contains")), "contains")
            return {**target, "kind": "wildcard", "wildcard": {"match": match, "value": str(text)}}
        self.report.unsupported.append(f"{label}: filter kind {kind!r}")
        return None

    @staticmethod
    def ref_expr(target: Mapping[str, Any]) -> str:
        if "field" in target:
            return "[" + str(target["field"]).replace("]", "]]") + "]"
        return str(target["expr"])

    def measure_name_filter(self, flt: Mapping[str, Any]) -> None:
        """Measure Names on the view: its filter lists the measures behind Measure Values."""
        for member in flt.get("members", []) or []:
            pill = _pill(str(tableau_literal(member) or ""))
            if pill is not None:
                self.add(
                    {
                        "field": pill.local,
                        "datasource": pill.datasource,
                        "derivation": pill.derivation,
                        "kind": pill.kind,
                    },
                    "Measure Values",
                )

    # -- sorts -------------------------------------------------------------------------------------------
    def sorts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sort in self.ws.get("sorts", []) or []:
            pill = _pill(str(sort.get("field", "")))
            by = self.outputs.get(pill) if pill else None
            if by is None:
                continue
            direction = "desc" if str(sort.get("direction", "ASC")).upper() == "DESC" else "asc"
            spec: dict[str, Any] = {"by": by, "dir": direction}
            kind = sort.get("kind")
            if kind in ("computed", "shelf") and sort.get("using"):
                using = _pill(str(sort["using"]))
                expr = self.aggregate_expr(using) if using else None
                if expr is None:
                    self.report.notes.append(f"sort of {by!r} by {sort['using']} kept as alphabetic")
                else:
                    spec["expr"] = expr
            elif kind == "manual":
                spec["order"] = [tableau_literal(m) for m in sort.get("order", []) if m != "%all%"]
                spec.pop("dir")
            out.append(spec)
        return out


def worksheet_query(
    ir: Mapping[str, Any],
    worksheet: str,
    model: GenerationReport,
    *,
    datasource: str | None = None,
) -> tuple[dict[str, Any], WorksheetReport]:
    """The query behind one worksheet, against the model generated for its primary data source.

    ``model`` is the ``GenerationReport`` from ``generate_model`` for that data source (it carries the
    local-name mapping). ``datasource`` defaults to the worksheet's first non-parameter data source.
    """
    sheet = next((w for w in ir.get("worksheets", []) if w.get("name") == worksheet), None)
    if sheet is None:
        raise ValueError(f"no worksheet {worksheet!r}")
    sources = [d for d in sheet.get("datasources", []) if d != "Parameters"]
    primary = datasource or (sources[0] if sources else "")
    # Worksheets name a data source by caption or internal name; pills always use the internal one.
    for ds in ir.get("datasources", []):
        if primary in (ds.get("caption"), ds.get("name")):
            primary = str(ds.get("name"))
    conv = _Converter(sheet, primary, model)
    conv.shelves()
    where = conv.filters()
    if conv.measure_names and not any(isinstance(m, dict) or m for m in conv.measures):
        conv.report.unsupported.append("Measure Names on the view with no measure list")
    query: dict[str, Any] = {}
    if conv.dimensions:
        query["dimensions"] = conv.dimensions
    if conv.measures:
        query["measures"] = conv.measures
    if where:
        query["where"] = where
    sorts = conv.sorts()
    if sorts:
        query["sort"] = sorts
    totals = sheet.get("totals") or {}
    if totals.get("rows") or totals.get("cols") or totals.get("grand"):
        query["totals"] = {"grand": True}
        conv.report.notes.append("grand totals converted; subtotal placement needs review")
    if sheet.get("aggregated") is False:
        conv.report.notes.append("the worksheet is disaggregated (Analysis > Aggregate Measures off)")
    if not query.get("dimensions") and not query.get("measures"):
        conv.report.unsupported.append("no fields on the view")
    return query, conv.report
