"""Parse Tableau workbooks and data sources (.twb/.twbx/.tds/.tdsx) into the Tableau IR.

The IR (``tabfree/tableau-ir@0``, see packages/schemas) keeps Tableau's own names and encodings
so later steps can generate semantic models and dashboard manifests deterministically
(BUILD_PLAN P1.8 / P2.1). Files are untrusted: XML is parsed with defusedxml, packages are read
in memory with a size cap, and credentials (password/token/secret attributes) are never copied.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeElementTree

from tabfree_migrate import __version__

MAX_MEMBER_BYTES = 256 * 1024 * 1024
_FCP = re.compile(r"^_\.fcp\.[A-Za-z0-9]+\.(?:true|false)\.\.\.(.+)$")
_INSTANCE_REF = re.compile(r"\[[^\[\]]+\](?:\.\[[^\[\]]+\])+")
_SECRET_ATTR = re.compile(r"password|token|secret", re.IGNORECASE)
_USER_FUNCTIONS = re.compile(r"\b(USERNAME|FULLNAME|USERDOMAIN|ISMEMBEROF|USERATTRIBUTE\w*)\s*\(", re.I)
_LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.I)
_TABLE_CALC = re.compile(
    r"\b(RUNNING_\w+|WINDOW_\w+|RANK\w*|INDEX|FIRST|LAST|SIZE|LOOKUP|PREVIOUS_VALUE|TOTAL)\s*\(",
    re.I,
)
_KIND_BY_TYPE = {"quantitative": "qk", "nominal": "nk", "ordinal": "ok"}
_DATATYPES = {"string", "integer", "real", "boolean", "date", "datetime", "spatial", "table"}
_RELATION_TYPES = {"table", "join", "union", "text", "pivot", "collection", "stored-proc"}
_MARKS = {
    "Automatic", "Text", "Icon", "Shape", "Rectangle", "Bar", "GanttBar", "Square", "Circle",
    "Heatmap", "PolyLine", "Line", "Polygon", "Area", "Pie", "Multipolygon", "VizExtension",
}  # fmt: skip
_ENCODINGS = {
    "color", "size", "text", "lod", "tooltip", "shape", "path", "angle", "wedge-size", "geometry",
}  # fmt: skip
_FILTER_CLASSES = {"categorical", "quantitative", "relative-date"}
_TRIGGERS = {"on-select": "select", "on-hover": "hover", "on-menu": "menu"}


class TableauFileError(ValueError):
    """The file is not a readable Tableau workbook or data source."""


# --- loading ---------------------------------------------------------------------------------------


def _normalize(element: Element) -> None:
    """Rename feature-flag elements such as ``_.fcp.Flag.true...relation`` to ``relation``."""
    for node in element.iter():
        if isinstance(node.tag, str) and (match := _FCP.match(node.tag)):
            node.tag = match.group(1)


def _read_document(path: Path) -> tuple[bytes, bool]:
    suffix = path.suffix.lower()
    if suffix in {".twb", ".tds"}:
        return path.read_bytes(), False
    if suffix not in {".twbx", ".tdsx"}:
        raise TableauFileError(f"unsupported file type: {path.name}")
    inner = ".twb" if suffix == ".twbx" else ".tds"
    try:
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith(inner)]
            if not members:
                raise TableauFileError(f"no {inner} file inside {path.name}")
            member = min(members, key=lambda m: m.filename.count("/"))
            if member.file_size > MAX_MEMBER_BYTES:
                raise TableauFileError(f"{member.filename} is too large")
            return archive.read(member), True
    except zipfile.BadZipFile as exc:
        raise TableauFileError(f"{path.name} is not a valid package: {exc}") from None


def load_root(path: Path) -> tuple[Element, bytes, bool]:
    data, packaged = _read_document(path)
    try:
        root = SafeElementTree.fromstring(data, forbid_entities=True, forbid_external=True)
    except Exception as exc:  # defusedxml and expat raise several unrelated types
        raise TableauFileError(f"{path.name}: {type(exc).__name__}: {exc}") from None
    _normalize(root)
    if root.tag not in {"workbook", "datasource"}:
        raise TableauFileError(f"{path.name}: unexpected root element <{root.tag}>")
    return root, data, packaged


# --- small helpers -----------------------------------------------------------------------------------


def _attrs(element: Element, *skip: str) -> dict[str, str]:
    return {
        k: v
        for k, v in element.attrib.items()
        if k not in skip and not _SECRET_ATTR.search(k) and not k.startswith("{")
    }


def _set(target: dict[str, Any], key: str, value: Any) -> None:
    if value not in (None, "", [], {}):
        target[key] = value


def _text_of(element: Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


# --- data sources ------------------------------------------------------------------------------------


def _connection(element: Element) -> dict[str, Any]:
    attrs = _attrs(element)
    conn: dict[str, Any] = {"class": attrs.pop("class", "unknown")}
    for key in (
        "server",
        "dbname",
        "schema",
        "warehouse",
        "username",
        "authentication",
        "filename",
    ):
        _set(conn, key, attrs.pop(key, None))
    _set(conn, "port", attrs.pop("port", None))
    if (initial := element.find("initial-sql")) is not None:
        _set(conn, "initialSql", _text_of(initial))
    _set(conn, "attributes", attrs)
    return conn


def _expression(element: Element | None) -> dict[str, Any] | None:
    if element is None:
        return None
    node: dict[str, Any] = {"op": element.get("op", "")}
    children = [_expression(child) for child in element.findall("expression")]
    _set(node, "args", [c for c in children if c])
    return node


def _relation(element: Element) -> dict[str, Any]:
    kind = element.get("type", "other")
    rel: dict[str, Any] = {"type": kind if kind in _RELATION_TYPES else "other"}
    for key in ("name", "connection", "table"):
        _set(rel, key, element.get(key))
    if kind == "text":
        _set(rel, "sql", (element.text or "").strip())
    join = element.get("join")
    if join in {"inner", "left", "right", "full"}:
        rel["join"] = join
    clause = element.find("clause")
    if clause is not None:
        _set(rel, "on", _expression(clause.find("expression")))
    _set(rel, "children", [_relation(child) for child in element.findall("relation")])
    if rel["type"] == "other":
        rel["raw"] = _attrs(element)
    return rel


def _object_graph(element: Element) -> dict[str, Any]:
    objects = []
    for obj in element.findall("objects/object"):
        relation = obj.find("properties/relation")
        if relation is None:
            relation = obj.find(".//relation")
        if relation is None:
            continue
        entry: dict[str, Any] = {"id": obj.get("id", ""), "relation": _relation(relation)}
        _set(entry, "caption", obj.get("caption"))
        objects.append(entry)
    relationships = []
    for rel in element.findall("relationships/relationship"):
        first = rel.find("first-end-point")
        second = rel.find("second-end-point")
        if first is None or second is None:
            continue
        item: dict[str, Any] = {
            "first": first.get("object-id", ""),
            "second": second.get("object-id", ""),
        }
        _set(item, "expression", _expression(rel.find("expression")))
        relationships.append(item)
    graph: dict[str, Any] = {"objects": objects}
    _set(graph, "relationships", relationships)
    return graph


def _column(element: Element) -> dict[str, Any]:
    col: dict[str, Any] = {"name": element.get("name", "")}
    _set(col, "caption", element.get("caption"))
    datatype = element.get("datatype")
    if datatype:
        col["datatype"] = datatype if datatype in _DATATYPES else "unknown"
    if element.get("role") in {"dimension", "measure"}:
        col["role"] = element.get("role")
    if element.get("type") in {"nominal", "ordinal", "quantitative"}:
        col["type"] = element.get("type")
    _set(col, "aggregation", element.get("aggregation"))
    _set(col, "defaultFormat", element.get("default-format"))
    _set(col, "semanticRole", element.get("semantic-role"))
    if element.get("hidden") == "true":
        col["hidden"] = True
    if (desc := element.find("desc")) is not None:
        _set(col, "description", _text_of(desc))
    aliases = {a.get("key", ""): a.get("value", "") for a in element.findall("aliases/alias")}
    _set(col, "aliases", aliases)
    calc = element.find("calculation")
    if calc is not None:
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
        raw = _attrs(calc, "class", "formula")
        _set(info, "raw", raw)
        col["calculation"] = info
    domain = element.get("param-domain-type")
    if domain in {"list", "range", "any"}:
        param: dict[str, Any] = {"domain": domain}
        _set(param, "members", [m.get("value", "") for m in element.findall("members/member")])
        if (rng := element.find("range")) is not None:
            param["range"] = _attrs(rng)
        if element.get("value") is not None:
            param["value"] = element.get("value")
        col["param"] = param
    return col


def _datasource(element: Element) -> dict[str, Any]:
    name = element.get("name", "")
    ds: dict[str, Any] = {"name": name, "kind": "embedded"}
    _set(ds, "caption", element.get("caption"))
    if name == "Parameters":
        ds["kind"] = "parameters"
    connection = element.find("connection")
    if connection is not None:
        ds["connection"] = _connection(connection)
        if connection.get("class") == "sqlproxy":
            ds["kind"] = "published"
            published: dict[str, Any] = {}
            _set(published, "contentUrl", connection.get("dbname"))
            location = element.find("repository-location")
            if location is not None:
                _set(published, "siteContentUrl", location.get("site"))
            _set(ds, "published", published)
        named = []
        for named_connection in connection.findall("named-connections/named-connection"):
            inner = named_connection.find("connection")
            if inner is not None:
                named.append(_connection(inner) | {"name": named_connection.get("name", "")})
        _set(ds, "namedConnections", named)
        relation = connection.find("relation")
        if relation is not None:
            ds["relation"] = _relation(relation)
    graph = element.find("object-graph")
    if graph is not None:
        ds["objectGraph"] = _object_graph(graph)
    ds["columns"] = [_column(c) for c in element.findall("column")]
    drill = [
        {"name": d.get("name", ""), "fields": [_text_of(f) for f in d.findall("field")]}
        for d in element.findall("drill-paths/drill-path")
    ]
    _set(ds, "drillPaths", drill)
    _set(ds, "groups", [_attrs(g) for g in element.findall("group")])
    _set(ds, "filters", [_attrs(f) for f in element.findall("filter")])
    extract = element.find("extract")
    if extract is not None:
        info: dict[str, Any] = {"enabled": extract.get("enabled") == "true"}
        conn = extract.find("connection")
        if conn is not None:
            _set(info, "file", conn.get("dbname"))
        refresh = extract.find("refresh")
        if refresh is not None:
            info["refresh"] = _attrs(refresh)
            _set(info, "incrementalKey", refresh.get("increment-key"))
        _set(info, "filters", [_attrs(f) for f in extract.findall(".//filter")])
        ds["extract"] = info
    return ds


# --- worksheets --------------------------------------------------------------------------------------


def _shelf(element: Element | None) -> dict[str, Any] | None:
    text = (element.text or "").strip() if element is not None else ""
    if not text:
        return None
    return {"expr": text, "instances": _INSTANCE_REF.findall(text)}


def _worksheet_filter(element: Element) -> dict[str, Any] | None:
    cls = element.get("class")
    if cls not in _FILTER_CLASSES:
        return None
    flt: dict[str, Any] = {"field": element.get("column", ""), "class": cls}
    if element.get("context") == "true":
        flt["context"] = True
    members = [g.get("member") for g in element.iter("groupfilter") if g.get("member") is not None]
    _set(flt, "members", members)
    if (gf := element.find("groupfilter")) is not None and gf.get("function") == "except":
        flt["exclude"] = True
    for key in ("min", "max"):
        if (node := element.find(key)) is not None:
            flt[key] = _text_of(node)
    if element.get("included-values") == "in-range-or-null":
        flt["includeNulls"] = True
    _set(flt, "period", element.get("period-type"))
    for attr, key in (("first-period", "firstPeriod"), ("last-period", "lastPeriod")):
        if (value := element.get(attr)) is not None and re.fullmatch(r"-?\d+", value):
            flt[key] = int(value)
    _set(flt, "anchor", element.get("anchor"))
    raw = _attrs(element, "class", "column")
    for node in element.iter("groupfilter"):
        if node.get("function") in {"end", "order"} or node.get("count"):
            flt["topN"] = _attrs(node)
    _set(flt, "raw", raw)
    return flt


def _worksheet(element: Element, hidden: set[str]) -> dict[str, Any]:
    name = element.get("name", "")
    view = element.find("table/view")
    ws: dict[str, Any] = {"name": name, "datasources": [], "panes": [], "filters": []}
    title = element.find("layout-options/title")
    if title is not None:
        _set(ws, "title", _text_of(title))
    if name in hidden:
        ws["hidden"] = True
    if view is not None:
        ws["datasources"] = [d.get("name", "") for d in view.findall("datasources/datasource")]
        deps: dict[str, list[str]] = {}
        instances = []
        for block in view.findall("datasource-dependencies"):
            ds_name = block.get("datasource", "")
            deps[ds_name] = [c.get("name", "") for c in block.findall("column")]
            for inst in block.findall("column-instance"):
                entry: dict[str, Any] = {
                    "ref": f"[{ds_name}].{inst.get('name', '')}",
                    "datasource": ds_name,
                    "field": inst.get("column", ""),
                    "derivation": inst.get("derivation", "None"),
                }
                _set(entry, "pivot", inst.get("pivot"))
                if inst.get("type") in _KIND_BY_TYPE:
                    entry["kind"] = _KIND_BY_TYPE[str(inst.get("type"))]
                if (calc := inst.find("table-calc")) is not None:
                    entry["tableCalc"] = _attrs(calc)
                instances.append(entry)
        _set(ws, "dependencies", deps)
        _set(ws, "fieldInstances", instances)
        filters = [_worksheet_filter(f) for f in view.findall("filter")]
        ws["filters"] = [f for f in filters if f]
        sorts = [_attrs(s) | {"kind": s.tag} for s in view if s.tag.endswith("sort")]
        _set(ws, "sorts", sorts)
    table = element.find("table")
    if table is not None:
        for key, tag in (("rows", "rows"), ("cols", "cols"), ("pages", "pages")):
            _set(ws, key, _shelf(table.find(tag)))
        for pane in table.findall("panes/pane"):
            mark = pane.find("mark")
            mark_class = mark.get("class", "Automatic") if mark is not None else "Automatic"
            entry = {"markClass": mark_class if mark_class in _MARKS else "Automatic"}
            encodings: dict[str, list[str]] = {}
            for enc in pane.findall("encodings/*"):
                if enc.tag in _ENCODINGS and enc.get("column"):
                    key = "wedgeSize" if enc.tag == "wedge-size" else enc.tag
                    encodings.setdefault(key, []).append(str(enc.get("column")))
            _set(entry, "encodings", encodings)
            _set(entry, "id", pane.get("id"))
            ws["panes"].append(entry)
    return ws


# --- dashboards, actions, stories ------------------------------------------------------------------


def _zone(element: Element) -> dict[str, Any]:
    zone_type = element.get("type-v2") or element.get("type") or "viz"
    zone: dict[str, Any] = {"type": zone_type}
    for key in ("x", "y", "w", "h"):
        zone[key] = int(element.get(key, "0") or 0)
    _set(zone, "id", element.get("id"))
    _set(zone, "name", element.get("name"))
    if zone_type == "viz" and element.get("name"):
        zone["worksheet"] = element.get("name")
    if element.get("is-floating") == "true" or element.get("floating") == "true":
        zone["floating"] = True
    if element.get("hidden-by-user") == "true":
        zone["hidden"] = True
    param = element.get("param")
    if zone_type == "layout-flow":
        layout: dict[str, Any] = {}
        if param in {"horz", "vert"}:
            layout["direction"] = param
        if element.get("layout-strategy-id") == "distribute-evenly":
            layout["distribute"] = True
        _set(zone, "layout", layout)
    elif zone_type == "paramctrl":
        _set(zone, "param", param)
    elif param:
        zone["field"] = param
    _set(zone, "mode", element.get("mode"))
    if zone_type == "text":
        _set(zone, "text", _text_of(element.find("formatted-text")))
    if zone_type == "bitmap":
        _set(zone, "image", element.get("param"))
    if zone_type == "web":
        _set(zone, "url", element.get("param"))
    _set(zone, "children", [_zone(child) for child in element.findall("zone")])
    return zone


def _dashboard(element: Element) -> dict[str, Any]:
    dash: dict[str, Any] = {"name": element.get("name", ""), "zones": []}
    title = element.find("layout-options/title")
    if title is not None:
        _set(dash, "title", _text_of(title))
    size = element.find("size")
    if size is not None:
        info: dict[str, Any] = {}
        mode = size.get("sizing-mode")
        if mode in {"fixed", "range", "automatic"}:
            info["mode"] = mode
        for attr, key in (
            ("maxwidth", "width"), ("maxheight", "height"), ("minwidth", "minWidth"),
            ("minheight", "minHeight"), ("maxwidth", "maxWidth"), ("maxheight", "maxHeight"),
        ):  # fmt: skip
            if (value := size.get(attr)) and value.isdigit():
                info[key] = int(value)
        _set(dash, "size", info)
    dash["zones"] = [_zone(z) for z in element.findall("zones/zone")]
    layouts = []
    for device in element.findall("devicelayouts/devicelayout"):
        name = (device.get("name") or "").lower()
        kind = "phone" if "phone" in name else "tablet" if "tablet" in name else "desktop"
        layouts.append({"device": kind, "zones": [_zone(z) for z in device.findall("zones/zone")]})
    _set(dash, "deviceLayouts", layouts)
    return dash


def _action_type(command: str) -> str:
    if "tsl-filter" in command:
        return "filter"
    if "brush" in command or "highlight" in command:
        return "highlight"
    if "url" in command or "browse" in command:
        return "url"
    if "goto" in command or "navigate" in command:
        return "sheet-navigate"
    if "parameter" in command:
        return "edit-parameter"
    if "group" in command or "set" in command:
        return "edit-group"
    return "unknown"


def _action(element: Element) -> dict[str, Any]:
    command_el = element.find("command")
    link = element.find("link")
    command = command_el.get("command", "") if command_el is not None else ""
    act: dict[str, Any] = {
        "name": element.get("name", ""),
        "type": "url" if link is not None and not command else _action_type(command),
    }
    _set(act, "caption", element.get("caption"))
    activation = element.find("activation")
    if activation is not None and activation.get("type") in _TRIGGERS:
        act["trigger"] = _TRIGGERS[str(activation.get("type"))]
    source = element.find("source")
    if source is not None:
        src: dict[str, Any] = {}
        _set(src, "dashboard", source.get("dashboard"))
        _set(src, "worksheets", [s.get("name", "") for s in source.findall("worksheet")])
        _set(act, "source", src)
    _set(act, "command", command)
    params = {p.get("name", ""): p.get("value", "") for p in element.findall("command/param")}
    target: dict[str, Any] = {}
    _set(target, "dashboard", params.get("target"))
    if link is not None:
        _set(target, "url", link.get("expression"))
    _set(act, "target", target)
    _set(act, "params", params)
    return act


def _story(element: Element) -> dict[str, Any]:
    points = []
    for point in element.iter("story-point"):
        entry: dict[str, Any] = {}
        _set(entry, "caption", point.get("caption"))
        _set(entry, "target", point.get("captured-sheet"))
        points.append(entry)
    return {"name": element.get("name", ""), "points": points}


# --- entry point -----------------------------------------------------------------------------------


def parse_file(path: Path, *, tableau: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a Tableau file into an IR document (validate it with tabfree_schemas.validate)."""
    root, _data, packaged = load_root(path)
    source: dict[str, Any] = {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "packaged": packaged,
        "parser": f"tabfree-migrate {__version__}",
        "parsedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _set(source, "sourceBuild", root.get("source-build"))
    _set(source, "sourcePlatform", root.get("source-platform"))
    _set(source, "version", root.get("version"))
    if tableau:
        source["tableau"] = tableau

    notes: list[dict[str, Any]] = []
    if root.tag == "datasource":
        return {
            "schema": "tabfree/tableau-ir@0",
            "kind": "datasource",
            "source": source,
            "datasources": [_datasource(root)],
            "notes": notes,
        }

    datasources = [_datasource(d) for d in root.findall("datasources/datasource")]
    parameters: list[dict[str, Any]] = next(
        (d["columns"] for d in datasources if d["kind"] == "parameters"), []
    )
    hidden = {
        w.get("name", "")
        for w in root.findall("windows/window")
        if w.get("hidden") == "true" or w.get("class") == "hidden-worksheet"
    }
    worksheets = [_worksheet(w, hidden) for w in root.findall("worksheets/worksheet")]
    dashboards, stories = [], []
    for element in root.findall("dashboards/dashboard"):
        if element.get("type") == "storyboard":
            stories.append(_story(element))
        else:
            dashboards.append(_dashboard(element))
    actions = [_action(a) for a in root.findall("actions/action")]
    for action in actions:
        if action["type"] == "unknown":
            notes.append(
                {"level": "unsupported", "path": f"actions/{action['name']}",
                 "message": f"unrecognised action command {action.get('command', '')!r}"}
            )  # fmt: skip
    for ws in worksheets:
        if any(p.get("markClass") == "VizExtension" for p in ws["panes"]):
            notes.append(
                {"level": "unsupported", "path": f"worksheets/{ws['name']}",
                 "message": "viz extension mark: needs a custom TabFree component"}
            )  # fmt: skip
    return {
        "schema": "tabfree/tableau-ir@0",
        "kind": "workbook",
        "source": source,
        "datasources": datasources,
        "parameters": parameters,
        "worksheets": worksheets,
        "dashboards": dashboards,
        "actions": actions,
        "stories": stories,
        "notes": notes,
    }
