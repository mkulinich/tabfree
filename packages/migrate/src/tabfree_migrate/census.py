"""Construct census and complexity scoring over parsed Tableau IR (BUILD_PLAN P1.6).

The census counts every construct the converter must handle (calculation functions, LODs,
table calculations, mark types, filter classes, action types, ...). Across an estate it sizes
the P0 backlog from real data instead of vendor rubrics. The complexity score ranks workbooks
for migration waves; weights are a first calibration (see tableau_server_apis.md, Q10).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

_FUNCTION = re.compile(r"\b([A-Z_][A-Z0-9_]*)\s*\(", re.IGNORECASE)
# Field references ([Name (Owner)]), string literals and comments are not function calls.
_NOT_CODE = re.compile(r"\[[^\]]*\]|\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|//[^\n]*")


def functions_in(formula: str) -> list[str]:
    """Upper-cased function names called in a Tableau formula."""
    return [name.upper() for name in _FUNCTION.findall(_NOT_CODE.sub(" ", formula))]


_LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.IGNORECASE)
TABLE_CALC_FUNCTIONS = frozenset(
    {
        "RUNNING_SUM", "RUNNING_AVG", "RUNNING_MIN", "RUNNING_MAX", "RUNNING_COUNT",
        "WINDOW_SUM", "WINDOW_AVG", "WINDOW_MIN", "WINDOW_MAX", "WINDOW_COUNT", "WINDOW_MEDIAN",
        "WINDOW_STDEV", "WINDOW_STDEVP", "WINDOW_VAR", "WINDOW_VARP", "WINDOW_PERCENTILE",
        "WINDOW_CORR", "WINDOW_COVAR", "WINDOW_COVARP", "RANK", "RANK_DENSE", "RANK_MODIFIED",
        "RANK_PERCENTILE", "RANK_UNIQUE", "INDEX", "FIRST", "LAST", "SIZE", "LOOKUP",
        "PREVIOUS_VALUE", "TOTAL",
    }
)  # fmt: skip
USER_FUNCTIONS = frozenset({"USERNAME", "FULLNAME", "USERDOMAIN", "ISMEMBEROF", "USERATTRIBUTE",
                            "USERATTRIBUTEINCLUDES"})  # fmt: skip
HARD_FUNCTIONS = frozenset(
    {"RAWSQL_BOOL", "RAWSQL_DATE", "RAWSQL_DATETIME", "RAWSQL_INT", "RAWSQL_REAL", "RAWSQL_STR",
     "RAWSQLAGG_BOOL", "RAWSQLAGG_DATE", "RAWSQLAGG_DATETIME", "RAWSQLAGG_INT", "RAWSQLAGG_REAL",
     "RAWSQLAGG_STR", "SCRIPT_BOOL", "SCRIPT_INT", "SCRIPT_REAL", "SCRIPT_STR", "MAKEPOINT",
     "MAKELINE", "BUFFER", "DISTANCE", "MODEL_PERCENTILE", "MODEL_QUANTILE"}
)  # fmt: skip

#: Complexity weights per construct (first calibration; tune against real migration effort).
WEIGHTS: dict[str, float] = {
    "worksheets": 1.0,
    "dashboards": 2.0,
    "calculated_fields": 0.5,
    "lod_expressions": 4.0,
    "table_calculations": 3.0,
    "quick_table_calcs": 2.0,
    "parameters": 1.0,
    "actions": 2.0,
    "blended_worksheets": 5.0,
    "custom_sql": 3.0,
    "user_functions": 4.0,
    "hard_functions": 8.0,
    "maps": 2.0,
    "extensions": 6.0,
    "stories": 3.0,
    "device_layouts": 1.0,
}


@dataclass
class Census:
    """Counts of constructs in one workbook or across many (combine with ``+``)."""

    totals: Counter[str] = field(default_factory=Counter)
    functions: Counter[str] = field(default_factory=Counter)
    marks: Counter[str] = field(default_factory=Counter)
    filters: Counter[str] = field(default_factory=Counter)
    actions: Counter[str] = field(default_factory=Counter)
    connections: Counter[str] = field(default_factory=Counter)
    zones: Counter[str] = field(default_factory=Counter)

    def __add__(self, other: Census) -> Census:
        return Census(
            self.totals + other.totals,
            self.functions + other.functions,
            self.marks + other.marks,
            self.filters + other.filters,
            self.actions + other.actions,
            self.connections + other.connections,
            self.zones + other.zones,
        )

    def score(self) -> float:
        return round(sum(WEIGHTS.get(k, 0.0) * v for k, v in self.totals.items()), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score(),
            "totals": dict(self.totals),
            "functions": dict(self.functions.most_common()),
            "marks": dict(self.marks),
            "filters": dict(self.filters),
            "actions": dict(self.actions),
            "connections": dict(self.connections),
            "zones": dict(self.zones),
        }


def _walk_zones(zones: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for zone in zones:
        yield zone
        yield from _walk_zones(zone.get("children", []))


def _walk_relations(relation: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    if relation:
        yield relation
        for child in relation.get("children", []):
            yield from _walk_relations(child)


def census_of(ir: dict[str, Any]) -> Census:
    """Count constructs in one IR document."""
    c = Census()
    t = c.totals
    datasources = [d for d in ir.get("datasources", []) if d.get("kind") != "parameters"]
    t["datasources"] += len(datasources)
    t["parameters"] += len(ir.get("parameters", []))
    seen_formulas: set[tuple[str, str]] = set()
    for ds in datasources:
        if ds.get("extract", {}).get("enabled"):
            t["extracts"] += 1
        conn = ds.get("connection", {})
        classes = [n.get("class", "") for n in ds.get("namedConnections", [])] or [conn.get("class", "")]
        c.connections.update(cls for cls in classes if cls)
        relations = list(_walk_relations(ds.get("relation")))
        for obj in ds.get("objectGraph", {}).get("objects", []):
            relations += list(_walk_relations(obj.get("relation")))
        t["custom_sql"] += sum(1 for r in relations if r.get("type") == "text")
        t["relationships"] += len(ds.get("objectGraph", {}).get("relationships", []))
        for col in ds.get("columns", []):
            formula = col.get("calculation", {}).get("formula")
            if not formula or (ds["name"], col["name"]) in seen_formulas:
                continue
            seen_formulas.add((ds["name"], col["name"]))
            t["calculated_fields"] += 1
            names = functions_in(formula)
            c.functions.update(names)
            t["lod_expressions"] += len(_LOD.findall(formula))
            t["table_calculations"] += sum(1 for n in names if n in TABLE_CALC_FUNCTIONS)
            t["user_functions"] += sum(1 for n in names if n in USER_FUNCTIONS)
            t["hard_functions"] += sum(1 for n in names if n in HARD_FUNCTIONS)

    for ws in ir.get("worksheets", []):
        t["worksheets"] += 1
        if ws.get("hidden"):
            t["hidden_worksheets"] += 1
        if len([d for d in ws.get("datasources", []) if d != "Parameters"]) > 1:
            t["blended_worksheets"] += 1
        t["quick_table_calcs"] += sum(1 for i in ws.get("fieldInstances", []) if i.get("tableCalc"))
        for pane in ws.get("panes", []):
            c.marks[pane.get("markClass", "Automatic")] += 1
            encodings = pane.get("encodings", {})
            if "geometry" in encodings or pane.get("markClass") in {"Multipolygon", "Polygon"}:
                t["maps"] += 1
            if pane.get("markClass") == "VizExtension":
                t["extensions"] += 1
        for flt in ws.get("filters", []):
            label = flt["class"] + (" (context)" if flt.get("context") else "")
            c.filters[label] += 1

    for dash in ir.get("dashboards", []):
        t["dashboards"] += 1
        t["device_layouts"] += len(dash.get("deviceLayouts", []))
        for zone in _walk_zones(dash.get("zones", [])):
            c.zones[zone["type"]] += 1
            if zone["type"] == "extension":
                t["extensions"] += 1
    for action in ir.get("actions", []):
        t["actions"] += 1
        c.actions[action["type"]] += 1
    t["stories"] += len(ir.get("stories", []))
    return c


def estate_census(irs: Iterable[dict[str, Any]]) -> Census:
    total = Census()
    for ir in irs:
        total = total + census_of(ir)
    return total
