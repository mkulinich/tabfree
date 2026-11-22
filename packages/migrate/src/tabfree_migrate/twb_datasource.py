"""Data sources: connections, relations, the object graph, columns, sets, folders and extracts."""

from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import Element

from tabfree_migrate.twb_sheets import sorts
from tabfree_migrate.twb_xml import (
    attrs,
    field_ref,
    flag,
    integer,
    number,
    put,
    set_function,
    summarise_set,
    table_calcs,
    text_of,
    typed_filter,
    ui_attrs,
)

_USER_FUNCTIONS = re.compile(r"\b(USERNAME|FULLNAME|USERDOMAIN|ISMEMBEROF|USERATTRIBUTE\w*)\s*\(", re.I)
_LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.I)
_TABLE_CALC = re.compile(
    r"\b(RUNNING_\w+|WINDOW_\w+|RANK\w*|INDEX|FIRST|LAST|SIZE|LOOKUP|PREVIOUS_VALUE|TOTAL)\s*\(",
    re.I,
)
_DATATYPES = {"string", "integer", "real", "boolean", "date", "datetime", "spatial", "table"}
_RELATION_TYPES = {"table", "join", "union", "text", "pivot", "collection", "stored-proc"}
_CONNECTION_KEYS = ("server", "dbname", "schema", "warehouse", "username", "authentication", "filename")


# --- connections and relations -----------------------------------------------------------------------


def connection(element: Element) -> dict[str, Any]:
    raw = attrs(element)
    conn: dict[str, Any] = {"class": raw.pop("class", "unknown")}
    for key in _CONNECTION_KEYS:
        put(conn, key, raw.pop(key, None))
    put(conn, "port", raw.pop("port", None))
    if (initial := element.find("initial-sql")) is not None:
        put(conn, "initialSql", text_of(initial))
    put(conn, "attributes", raw)
    return conn


def expression(element: Element | None) -> dict[str, Any] | None:
    """Tableau's join/relationship expression tree: ``{"op", "args"}``."""
    if element is None:
        return None
    node: dict[str, Any] = {"op": element.get("op", "")}
    children = [expression(child) for child in element.findall("expression")]
    put(node, "args", [c for c in children if c])
    return node


def clauses(tree: dict[str, Any] | None) -> list[dict[str, str]] | None:
    """Flatten ``AND`` of ``left <op> right`` comparisons between fields; None if not that shape."""
    if not tree:
        return None
    op, args = tree.get("op", ""), tree.get("args", [])
    if op.upper() == "AND":
        out: list[dict[str, str]] = []
        for arg in args:
            sub = clauses(arg)
            if sub is None:
                return None
            out += sub
        return out
    if len(args) == 2 and all(not a.get("args") for a in args) and op in {"=", "<>", "<", ">", "<=", ">="}:
        return [{"left": args[0]["op"], "op": op, "right": args[1]["op"]}]
    return None


def relation(element: Element) -> dict[str, Any]:
    kind = element.get("type", "other")
    rel: dict[str, Any] = {"type": kind if kind in _RELATION_TYPES else "other"}
    for key in ("name", "connection", "table"):
        put(rel, key, element.get(key))
    if kind == "text":
        put(rel, "sql", (element.text or "").strip())
    join = element.get("join")
    if join in {"inner", "left", "right", "full"}:
        rel["join"] = join
    elif kind == "join" and join is None:
        rel["join"] = "inner"
    clause = element.find("clause")
    if clause is not None:
        tree = expression(clause.find("expression"))
        put(rel, "on", tree)
        put(rel, "clauses", clauses(tree))
    if kind == "union" and element.get("all") is not None:
        rel["all"] = element.get("all") == "true"
    put(rel, "children", [relation(child) for child in element.findall("relation")])
    if rel["type"] in {"other", "pivot", "stored-proc", "union"}:
        put(rel, "raw", attrs(element, "type", "name", "connection", "table", "join", "all"))
    return rel


def _end_point(element: Element) -> tuple[str, str | None, str | None]:
    unique = element.get("unique-key")
    guaranteed = element.get("guaranteed-value")
    cardinality = None if unique is None else ("one" if unique == "true" else "many")
    integrity = None if guaranteed is None else ("all" if guaranteed == "true" else "some")
    return element.get("object-id", ""), cardinality, integrity


def object_graph(element: Element) -> dict[str, Any]:
    objects = []
    for obj in element.findall("objects/object"):
        props = obj.findall("properties")
        live = next((p for p in props if p.get("context", "") == ""), props[0] if props else None)
        rel = live.find("relation") if live is not None else obj.find(".//relation")
        if rel is None:
            continue
        entry: dict[str, Any] = {"id": obj.get("id", ""), "relation": relation(rel)}
        put(entry, "caption", obj.get("caption"))
        extract = next((p for p in props if p.get("context") == "extract"), None)
        if extract is not None and (ext_rel := extract.find("relation")) is not None:
            entry["extractRelation"] = relation(ext_rel)
        objects.append(entry)
    relationships = []
    for rel_el in element.findall("relationships/relationship"):
        first = rel_el.find("first-end-point")
        second = rel_el.find("second-end-point")
        if first is None or second is None:
            continue
        (first_id, first_card, first_int), (second_id, second_card, second_int) = (
            _end_point(first),
            _end_point(second),
        )
        item: dict[str, Any] = {"first": first_id, "second": second_id}
        tree = expression(rel_el.find("expression"))
        put(item, "expression", tree)
        put(item, "clauses", clauses(tree))
        cardinality: dict[str, str] = {}
        put(cardinality, "first", first_card)
        put(cardinality, "second", second_card)
        put(item, "cardinality", cardinality)
        integrity: dict[str, str] = {}
        put(integrity, "first", first_int)
        put(integrity, "second", second_int)
        put(item, "integrity", integrity)
        relationships.append(item)
    graph: dict[str, Any] = {"objects": objects}
    put(graph, "relationships", relationships)
    return graph


# --- columns -----------------------------------------------------------------------------------------


def _calculation(calc: Element) -> dict[str, Any]:
    formula = calc.get("formula")
    info: dict[str, Any] = {"class": calc.get("class", "tableau")}
    if formula:
        info["formula"] = formula
        if _USER_FUNCTIONS.search(formula):
            info["usesUserFunctions"] = True
        if _LOD.search(formula):
            info["lod"] = True
        if _TABLE_CALC.search(formula):
            info["tableCalc"] = True
    put(info, "tableCalcSettings", table_calcs(calc))
    skip: tuple[str, ...] = ("class", "formula")
    if info["class"] == "categorical-bin":
        skip += ("column", "new-bin", "default")
    elif info["class"] == "bin":
        skip += ("size", "size-parameter", "peg", "decimals")
    put(info, "raw", attrs(calc, *skip))
    return info


def _group(calc: Element) -> dict[str, Any]:
    """A categorical-bin calculation: Tableau's "group" of members under a new name."""
    group: dict[str, Any] = {"source": calc.get("column", "")}
    group["groups"] = [
        {"name": b.get("value", ""), "members": [text_of(v) for v in b.findall("value")]}
        for b in calc.findall("bin")
    ]
    if calc.get("default") is not None:
        group["other"] = calc.get("default")
    return group


def _bin(calc: Element) -> dict[str, Any]:
    info: dict[str, Any] = {"source": calc.get("formula", "")}
    put(info, "size", number(calc.get("size")))
    put(info, "sizeParameter", calc.get("size-parameter"))
    put(info, "peg", number(calc.get("peg")))
    put(info, "decimals", integer(calc.get("decimals")))
    return info


def _param(element: Element) -> dict[str, Any] | None:
    domain = element.get("param-domain-type")
    if domain not in {"list", "range", "any"}:
        return None
    param: dict[str, Any] = {"domain": domain}
    members = [m.get("value", "") for m in element.findall("members/member")]
    put(param, "members", members)
    members_el = element.findall("members/member")
    labels = {m.get("value", ""): m.get("alias", "") for m in members_el if m.get("alias")}
    put(param, "memberAliases", labels)
    if (rng := element.find("range")) is not None:
        param["range"] = attrs(rng)
    if element.get("value") is not None:
        param["value"] = element.get("value")
    put(param, "valueAlias", element.get("alias"))
    put(param, "sourceField", element.get("source-field"))
    put(param, "defaultValueField", element.get("default-value-field"))
    return param


def column(element: Element, folders: dict[str, str]) -> dict[str, Any]:
    col: dict[str, Any] = {"name": element.get("name", "")}
    put(col, "caption", element.get("caption"))
    datatype = element.get("datatype")
    if datatype:
        col["datatype"] = datatype if datatype in _DATATYPES else "unknown"
    if element.get("role") in {"dimension", "measure"}:
        col["role"] = element.get("role")
    if element.get("type") in {"nominal", "ordinal", "quantitative"}:
        col["type"] = element.get("type")
    put(col, "aggregation", element.get("aggregation"))
    put(col, "defaultFormat", element.get("default-format"))
    put(col, "semanticRole", element.get("semantic-role"))
    put(col, "visualTotals", element.get("visual-totals"))
    if element.get("hidden") == "true":
        col["hidden"] = True
    put(col, "folder", folders.get(col["name"]))
    if (desc := element.find("desc")) is not None:
        put(col, "description", text_of(desc))
    aliases = {a.get("key", ""): a.get("value", "") for a in element.findall("aliases/alias")}
    put(col, "aliases", aliases)
    calc = element.find("calculation")
    if calc is not None:
        col["calculation"] = _calculation(calc)
        if calc.get("class") == "categorical-bin":
            col["group"] = _group(calc)
        elif calc.get("class") == "bin":
            col["bin"] = _bin(calc)
    put(col, "param", _param(element))
    return col


# --- sets (<group>), folders, hierarchies -----------------------------------------------------------


def _set_kind(element: Element, tree: dict[str, Any] | None) -> str:
    ui = ui_attrs(element)
    if ui.get("auto-column"):
        return "internal"
    if ui.get("ui-builder") == "nest-group" or (tree and tree["function"] == "crossjoin"):
        return "combined"
    return "set"


def set_definition(element: Element) -> dict[str, Any]:
    """A data source ``<group>``: a set (members, condition or top N) or a combined field."""
    gf = element.find("groupfilter")
    tree = set_function(gf) if gf is not None else None
    entry: dict[str, Any] = {"name": element.get("name", ""), "kind": _set_kind(element, tree)}
    put(entry, "caption", element.get("caption"))
    if element.get("hidden") == "true":
        entry["hidden"] = True
    ui = ui_attrs(element)
    put(entry, "builder", ui.get("ui-builder"))
    put(entry, "autoColumn", ui.get("auto-column"))
    if tree is not None:
        entry.update(summarise_set(tree))
        entry["tree"] = tree
    put(entry, "raw", attrs(element, "name", "caption", "hidden", "name-style"))
    return entry


def _folders(element: Element) -> list[dict[str, Any]]:
    out = []
    for folder in element.findall("folder") + element.findall("folders-common/folder"):
        entry: dict[str, Any] = {"name": folder.get("name", "")}
        put(entry, "role", folder.get("role"))
        entry["fields"] = [item.get("name", "") for item in folder.findall("folder-item")]
        out.append(entry)
    return out


def _settings(element: Element) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    options = element.find("date-options")
    if options is None:
        options = element.find("overridable-settings/date-options")
    if options is not None:
        put(settings, "weekStart", options.get("start-of-week"))
        put(settings, "fiscalYearStart", options.get("fiscal-year-start"))
    put(settings, "dateFormat", text_of(element.find("overridable-settings/default-date-format")))
    return settings


def _extract(element: Element) -> dict[str, Any]:
    info: dict[str, Any] = {"enabled": element.get("enabled") == "true"}
    conn = element.find("connection")
    if conn is not None:
        put(info, "file", conn.get("dbname"))
        put(info, "engine", conn.get("class"))
        put(info, "updated", conn.get("update-time"))
    count = number(element.get("count"))
    if count is not None and count >= 0:
        info["limit"] = {"count": count, "units": element.get("units", "records")}
    if element.get("type") == "normalized":
        info["multipleTables"] = True
    put(info, "objectId", element.get("object-id"))
    refresh = element.find("refresh")
    if refresh is None and conn is not None:
        refresh = conn.find("refresh")
    if refresh is not None:
        info["refresh"] = attrs(refresh)
        incremental = refresh.get("incremental-updates") == "true"
        info["incremental"] = incremental
        put(info, "incrementalKey", refresh.get("increment-key"))
        events = []
        for event in refresh.findall("refresh-event"):
            item: dict[str, Any] = {"type": event.get("refresh-type", "")}
            put(item, "rows", integer(event.get("rows-inserted")))
            put(item, "started", event.get("timestamp-start"))
            put(item, "incrementValue", event.get("increment-value"))
            events.append(item)
        put(info, "refreshEvents", events)
    filters = [typed_filter(f) for f in element.findall("filter")]
    if conn is not None:
        filters += [typed_filter(f) for f in conn.findall("filter")]
    put(info, "filters", [f for f in filters if f])
    aggregations = [
        {"column": a.get("column", ""), "derivation": a.get("derivation", "")}
        for a in element.findall("aggregation")
    ]
    put(info, "aggregations", aggregations)
    if aggregations:
        info["aggregated"] = True
    put(info, "raw", attrs(element, "enabled", "count", "units", "type", "object-id"))
    return info


def datasource(element: Element) -> dict[str, Any]:
    name = element.get("name", "")
    ds: dict[str, Any] = {"name": name, "kind": "embedded"}
    put(ds, "caption", element.get("caption"))
    if name == "Parameters":
        ds["kind"] = "parameters"
    conn_el = element.find("connection")
    if conn_el is not None:
        ds["connection"] = connection(conn_el)
        if conn_el.get("class") == "sqlproxy":
            ds["kind"] = "published"
            published: dict[str, Any] = {}
            put(published, "contentUrl", conn_el.get("dbname"))
            location = element.find("repository-location")
            if location is not None:
                put(published, "siteContentUrl", location.get("site"))
            put(ds, "published", published)
        named = []
        for named_connection in conn_el.findall("named-connections/named-connection"):
            inner = named_connection.find("connection")
            if inner is not None:
                entry = connection(inner) | {"name": named_connection.get("name", "")}
                put(entry, "caption", named_connection.get("caption"))
                named.append(entry)
        put(ds, "namedConnections", named)
        rel = conn_el.find("relation")
        if rel is not None:
            ds["relation"] = relation(rel)
        put(ds, "metadataRecords", metadata_records(conn_el))
    graph = element.find("object-graph")
    if graph is not None:
        ds["objectGraph"] = object_graph(graph)
    folders = _folders(element)
    folder_of = {field: f["name"] for f in folders for field in f["fields"]}
    ds["columns"] = [column(c, folder_of) for c in element.findall("column")]
    drill = []
    for path in element.findall("drill-paths/drill-path"):
        drill.append({"name": path.get("name", ""), "fields": [text_of(f) for f in path.findall("field")]})
    put(ds, "drillPaths", drill)
    put(ds, "sets", [set_definition(g) for g in element.findall("group")])
    put(ds, "folders", folders)
    put(ds, "filters", [f for f in (typed_filter(x) for x in element.findall("filter")) if f])
    aliases = element.find("aliases")
    if aliases is not None and aliases.get("enabled") is not None:
        ds["aliasesEnabled"] = aliases.get("enabled") == "yes"
    put(ds, "settings", _settings(element))
    if (default_sorts := element.find("default-sorts")) is not None:
        put(ds, "defaultSorts", sorts(default_sorts))
    extract = element.find("extract")
    if extract is not None:
        ds["extract"] = _extract(extract)
    hidden = flag(element, "hasconnection")
    if hidden is False:
        ds["hasConnection"] = False
    return ds


def blend_relationships(root: Element) -> list[dict[str, Any]]:
    """Custom data blending links (``<datasource-relationships>``): primary → secondary fields."""
    out = []
    for rel in root.findall("datasource-relationships/datasource-relationship"):
        entry: dict[str, Any] = {"source": rel.get("source", ""), "target": rel.get("target", "")}
        mappings = rel.findall("column-mapping/map")
        entry["fields"] = [{"source": m.get("key", ""), "target": m.get("value", "")} for m in mappings]
        out.append(entry)
    return out


def metadata_records(conn_el: Element) -> list[dict[str, Any]]:
    """Physical columns from ``<metadata-records>``: remote name, parent table and types.

    Tableau's local name (``[Order Date]``) maps to the column's ``remote-name`` in the table named by
    ``parent-name``; the semantic-model generator uses this to place fields in tables.
    """
    out = []
    for record in conn_el.findall("metadata-records/metadata-record"):
        if record.get("class") != "column":
            continue
        entry: dict[str, Any] = {}
        for key, tag in (
            ("remoteName", "remote-name"),
            ("localName", "local-name"),
            ("parentName", "parent-name"),
            ("remoteType", "remote-type"),
            ("localType", "local-type"),
            ("aggregation", "aggregation"),
            ("ordinal", "ordinal"),
            ("objectId", "_.fcp.ObjectModelEncapsulateLegacy.true...object-id"),
        ):
            child = record.find(tag)
            if child is not None and child.text is not None:
                entry[key] = child.text
        if entry.get("objectId") is None and (child := record.find("object-id")) is not None and child.text:
            entry["objectId"] = child.text
        if entry.get("localName"):
            out.append(entry)
    return out


__all__ = ["blend_relationships", "connection", "datasource", "field_ref", "metadata_records", "relation"]
