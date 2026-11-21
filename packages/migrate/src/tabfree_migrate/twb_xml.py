"""Shared XML helpers for the TWB parser: attributes, set functions, filters and table calcs."""

from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import Element

from tabfree_migrate.fieldref import FieldRefError, parse_field_ref

USER_NS = "{http://www.tableausoftware.com/xml/user}"
_SECRET_ATTR = re.compile(r"password|token|secret", re.IGNORECASE)
_INT = re.compile(r"^-?\d+$")


def attrs(element: Element, *skip: str) -> dict[str, str]:
    """Attributes without credentials, namespaced (``user:``) attributes or ``skip``."""
    return {
        k: v
        for k, v in element.attrib.items()
        if k not in skip and not _SECRET_ATTR.search(k) and not k.startswith("{")
    }


def ui_attrs(element: Element) -> dict[str, str]:
    """Tableau's ``user:`` namespace attributes (UI hints such as ui-marker), without the prefix."""
    return {k[len(USER_NS) :]: v for k, v in element.attrib.items() if k.startswith(USER_NS)}


def put(target: dict[str, Any], key: str, value: Any) -> None:
    """Set ``key`` unless ``value`` is empty (None, "", [], {})."""
    if value not in (None, "", [], {}):
        target[key] = value


def text_of(element: Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def flag(element: Element, attr: str) -> bool | None:
    value = element.get(attr)
    if value in {"true", "yes", "1"}:
        return True
    if value in {"false", "no", "0"}:
        return False
    return None


def integer(value: str | None) -> int | None:
    if value is not None and _INT.match(value.strip()):
        return int(value)
    return None


def number(value: str | None) -> int | float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() and "." not in value and "e" not in value.lower() else parsed


def field_ref(text: str | None) -> dict[str, Any] | None:
    """A parsed field reference, or None when ``text`` is not one (e.g. a formula)."""
    if not text or not text.strip().startswith("["):
        return None
    try:
        return parse_field_ref(text)
    except FieldRefError:
        return None


# --- set functions (<groupfilter>) --------------------------------------------------------------------

_GF_KEYS = {
    "level": "level", "member": "member", "expression": "expression", "count": "count", "end": "end",
    "units": "units", "direction": "direction", "from": "from", "to": "to", "field": "field",
    "period-type-v2": "periodType", "first-period": "firstPeriod", "last-period": "lastPeriod",
    "include-future": "includeFuture", "period-anchor": "anchor",
}  # fmt: skip


def set_function(element: Element) -> dict[str, Any]:
    """A ``<groupfilter>`` tree as ``{"function", <attributes>, "ui", "args"}``."""
    node: dict[str, Any] = {"function": element.get("function", "")}
    raw: dict[str, str] = {}
    for key, value in attrs(element, "function").items():
        if key in _GF_KEYS:
            node[_GF_KEYS[key]] = value
        else:
            raw[key] = value
    put(node, "raw", raw)
    put(node, "ui", ui_attrs(element))
    put(node, "args", [set_function(child) for child in element.findall("groupfilter")])
    return node


def _walk(node: dict[str, Any]) -> list[dict[str, Any]]:
    out = [node]
    for child in node.get("args", []):
        out += _walk(child)
    return out


def _members(node: dict[str, Any]) -> list[str] | None:
    """Member literals of a ``member`` node or a ``union`` of them; None for anything else."""
    if node["function"] == "member" and "member" in node:
        return [node["member"]]
    if node["function"] == "union":
        values: list[str] = []
        for child in node.get("args", []):
            sub = _members(child)
            if sub is None:
                return None
            values += sub
        return values
    return None


def _top(node: dict[str, Any]) -> dict[str, Any]:
    top: dict[str, Any] = {"end": node.get("end", "top")}
    count = node.get("count", "")
    if (value := integer(count)) is not None:
        top["count"] = value
    elif count:
        top["countExpression"] = count
        ref = field_ref(count)
        if ref and ref.get("parameter"):
            top["countParameter"] = count
    put(top, "units", node.get("units"))
    order = next((c for c in node.get("args", []) if c["function"] == "order"), None)
    if order is not None:
        put(top, "by", order.get("expression"))
        put(top, "direction", order.get("direction"))
        level = next((c.get("level") for c in _walk(order) if c.get("level")), None)
        put(top, "level", level)
    return top


def summarise_set(tree: dict[str, Any]) -> dict[str, Any]:
    """Derive members/exclude/all/none/topN/condition/range/levels/wildcard from a set function."""
    out: dict[str, Any] = {}
    function = tree["function"]
    if (members := _members(tree)) is not None:
        out["members"] = members
    elif function == "except" and len(tree.get("args", [])) == 2:
        base, removed = tree["args"]
        if base["function"] == "level-members" and (members := _members(removed)) is not None:
            out["members"] = members
            out["exclude"] = True
        else:
            put(out, "levels", [n["level"] for n in _walk(tree) if n.get("level")])
    elif function == "level-members":
        out["all"] = True
    elif function == "empty-level":
        out["none"] = True
    elif function == "end":
        out["topN"] = _top(tree)
    elif function == "filter":
        put(out, "condition", tree.get("expression"))
    elif function == "range":
        out["range"] = {k: tree[k] for k in ("from", "to") if k in tree}
    elif function == "crossjoin":
        put(out, "levels", [c["level"] for c in tree.get("args", []) if c.get("level")])
    else:
        put(out, "levels", sorted({n["level"] for n in _walk(tree) if n.get("level")}))
    pattern = {
        k: v for node in _walk(tree) for k, v in node.get("ui", {}).items() if k.startswith("ui-pattern")
    }
    if pattern:
        out["wildcard"] = pattern
    conditions = [n["expression"] for n in _walk(tree) if n["function"] == "filter" and n.get("expression")]
    if conditions:
        out["condition"] = conditions[0]
    return out


# --- filters -----------------------------------------------------------------------------------------

_FILTER_CLASSES = {"categorical", "quantitative", "relative-date"}
_FILTER_KNOWN = {
    "class", "column", "context", "filter-group", "included-values", "period-type", "period-type-v2",
    "first-period", "last-period", "include-future", "include-null", "anchor", "period-anchor", "kind",
}  # fmt: skip


def typed_filter(element: Element) -> dict[str, Any] | None:
    """A worksheet, data source or extract ``<filter>`` as a typed filter (None if unclassed)."""
    cls = element.get("class")
    if cls not in _FILTER_CLASSES:
        return None
    flt: dict[str, Any] = {"field": element.get("column", ""), "class": cls, "kind": cls}
    put(flt, "ref", field_ref(element.get("column")))
    if element.get("context") == "true":
        flt["context"] = True
    put(flt, "filterGroup", integer(element.get("filter-group")))
    if element.get("kind") == "hide":
        flt["hide"] = True
    gf = element.find("groupfilter")
    if gf is not None:
        tree = set_function(gf)
        flt["tree"] = tree
        summary = summarise_set(tree)
        flt.update(summary)
        if cls == "categorical":
            if "topN" in summary:
                flt["kind"] = "top-n"
            elif "condition" in summary:
                flt["kind"] = "condition"
            elif "wildcard" in summary:
                flt["kind"] = "wildcard"
    for key in ("min", "max"):
        if (node := element.find(key)) is not None:
            flt[key] = text_of(node)
    included = element.get("included-values")
    put(flt, "includedValues", included)
    if included == "in-range-or-null":
        flt["includeNulls"] = True
    put(flt, "period", element.get("period-type-v2") or element.get("period-type"))
    for attr, key in (("first-period", "firstPeriod"), ("last-period", "lastPeriod")):
        put(flt, key, integer(element.get(attr)))
    put(flt, "anchor", element.get("period-anchor") or element.get("anchor"))
    for attr, key in (("include-future", "includeFuture"), ("include-null", "includeNull")):
        if (value := flag(element, attr)) is not None:
            flt[key] = value
    put(flt, "targets", [t.get("field", "") for t in element.findall("target")])
    put(flt, "raw", {k: v for k, v in attrs(element).items() if k not in _FILTER_KNOWN})
    return flt


# --- table calculations ------------------------------------------------------------------------------

_TC_KEYS = {
    "type": "type", "ordering-type": "orderingType", "ordering-field": "orderingField", "field": "field",
    "aggregation": "aggregation", "level-break": "restart", "level-address": "level", "from": "from",
    "to": "to", "window-options": "windowOptions", "diff-options": "diffOptions",
    "pcttotal-options": "pctTotalOptions", "tc-options": "options",
}  # fmt: skip


def table_calc(element: Element) -> dict[str, Any]:
    """A ``<table-calc>`` element: addressing, partitioning, level, restart, rank options..."""
    calc: dict[str, Any] = {}
    raw: dict[str, str] = {}
    for key, value in attrs(element).items():
        if key == "rank-options" and "," in value:
            method, direction = value.split(",", 1)
            calc["rank"] = {"method": method, "direction": direction}
        elif key in _TC_KEYS:
            calc[_TC_KEYS[key]] = value
        else:
            raw[key] = value
    put(calc, "addressing", [o.get("field", "") for o in element.findall("order")])
    if (sort := element.find("sort")) is not None:
        calc["sort"] = {"using": sort.get("using", ""), "direction": sort.get("direction", "")}
    if (address := element.find("address")) is not None:
        calc["address"] = [text_of(v) for v in address.findall("value")]
    put(calc, "raw", raw)
    return calc


def table_calcs(element: Element) -> dict[str, Any] | None:
    """The ``<table-calc>`` settings of a column instance or calculation.

    Entries with a ``field`` attribute configure table calcs nested in a calculated field (one per
    nested calc, under ``nested``). Of the others, the first is the primary calc and a second one
    its secondary calc (quick table calc plus secondary calculation).
    """
    calcs = [table_calc(tc) for tc in element.findall("table-calc")]
    if not calcs:
        return None
    own = [c for c in calcs if "field" not in c]
    nested = [c for c in calcs if "field" in c]
    primary: dict[str, Any] = own[0] if own else {}
    if len(own) > 1:
        primary["secondary"] = own[1]
    put(primary, "nested", nested)
    return primary
