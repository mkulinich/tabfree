"""Worksheets: shelves, panes and encodings, filters, sorts, table calcs and totals."""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

from tabfree_migrate.fieldref import FieldRefError, parse_field_ref, parse_shelf, shelf_refs
from tabfree_migrate.twb_xml import field_ref, flag, integer, put, table_calcs, text_of, typed_filter

_KIND_BY_TYPE = {"quantitative": "qk", "nominal": "nk", "ordinal": "ok"}
_MARKS = {
    "Automatic", "Text", "Icon", "Shape", "Rectangle", "Bar", "GanttBar", "Square", "Circle",
    "Heatmap", "PolyLine", "Line", "Polygon", "Area", "Pie", "Multipolygon", "VizExtension",
}  # fmt: skip
_ENCODINGS = {
    "color", "size", "text", "lod", "tooltip", "shape", "path", "angle", "wedge-size", "geometry",
}  # fmt: skip
_SORT_KINDS = {"computed-sort": "computed", "manual-sort": "manual", "alphabetic-sort": "alphabetic",
               "natural-sort": "natural"}  # fmt: skip


def _ref(text: str) -> dict[str, Any]:
    """A parsed field reference; text that does not parse is kept as ``{"ref": text}``."""
    return field_ref(text) or {"ref": text}


def shelf(element: Element | None, notes: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
    text = (element.text or "").strip() if element is not None else ""
    if not text:
        return None
    entry: dict[str, Any] = {"expr": text}
    try:
        tree = parse_shelf(text)
    except FieldRefError as exc:
        notes.append({"level": "warning", "path": path, "message": f"shelf not parsed: {exc}"})
        return entry
    entry["instances"] = [r["ref"] for r in shelf_refs(tree)]
    entry["tree"] = tree
    return entry


def _instance(block_ds: str, inst: Element) -> dict[str, Any]:
    name = inst.get("name", "")
    entry: dict[str, Any] = {
        "ref": f"[{block_ds}].{name}",
        "datasource": block_ds,
        "field": inst.get("column", ""),
        "derivation": inst.get("derivation", "None"),
    }
    try:
        parsed = parse_field_ref(name)
    except FieldRefError:
        parsed = {}
    put(entry, "code", parsed.get("derivation"))
    put(entry, "quickTableCalc", parsed.get("tableCalc"))
    put(entry, "forecast", parsed.get("forecast"))
    put(entry, "index", parsed.get("index"))
    put(entry, "pivot", inst.get("pivot"))
    if inst.get("type") in _KIND_BY_TYPE:
        entry["kind"] = _KIND_BY_TYPE[str(inst.get("type"))]
    put(entry, "tableCalc", table_calcs(inst))
    return entry


def sorts(view: Element) -> list[dict[str, Any]]:
    """Sorts under a view or ``<default-sorts>``: computed, manual, alphabetic, natural, shelf."""
    sorts: list[dict[str, Any]] = []
    for node in view:
        kind = _SORT_KINDS.get(node.tag)
        if node.tag == "sort":
            kind = node.get("class") or "computed"
        if kind is None:
            continue
        item: dict[str, Any] = {"kind": kind, "field": node.get("column", "")}
        put(item, "direction", node.get("direction"))
        if using := node.get("using"):
            item["using"] = using
            ref = field_ref(using)
            if ref is not None and ref.get("derivation"):
                item["aggregation"] = ref.get("derivationName", ref["derivation"])
        put(item, "order", [text_of(b) for b in node.findall("dictionary/bucket")])
        sorts.append(item)
    for node in view.findall("shelf-sorts/shelf-sort-v2"):
        item = {"kind": "shelf", "field": node.get("dimension-to-sort", "")}
        put(item, "direction", node.get("direction"))
        put(item, "using", node.get("measure-to-sort-by"))
        put(item, "shelf", node.get("shelf"))
        if node.get("is-on-innermost-dimension") == "true":
            item["innermost"] = True
        sorts.append(item)
    return sorts


def _totals(table: Element) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for tag in ("rows", "cols"):
        node = table.find(tag)
        if node is not None and node.get("total") == "true":
            totals[tag] = True
    put(totals, "subtotals", [text_of(c) for c in table.findall("subtotals/column")])
    return totals


def _pane(pane: Element) -> dict[str, Any]:
    marks = pane.findall("mark")
    mark = marks[-1] if marks else None
    mark_class = mark.get("class", "Automatic") if mark is not None else "Automatic"
    entry: dict[str, Any] = {"markClass": mark_class if mark_class in _MARKS else "Automatic"}
    encodings: dict[str, list[dict[str, Any]]] = {}
    for enc in pane.findall("encodings/*"):
        if enc.tag in _ENCODINGS and enc.get("column"):
            key = "wedgeSize" if enc.tag == "wedge-size" else enc.tag
            encodings.setdefault(key, []).append(_ref(str(enc.get("column"))))
    put(entry, "encodings", encodings)
    put(entry, "id", pane.get("id"))
    axis: dict[str, Any] = {}
    put(axis, "x", pane.get("x-axis-name"))
    put(axis, "y", pane.get("y-axis-name"))
    put(axis, "yIndex", integer(pane.get("y-index")))
    put(entry, "axis", axis)
    breakdown = pane.find("view/breakdown")
    if breakdown is not None and breakdown.get("value") in {"on", "off"}:
        entry["stackMarks"] = breakdown.get("value") == "on"
    return entry


def worksheet(element: Element, hidden: set[str], notes: list[dict[str, Any]]) -> dict[str, Any]:
    name = element.get("name", "")
    path = f"worksheets/{name}"
    view = element.find("table/view")
    ws: dict[str, Any] = {"name": name, "datasources": [], "panes": [], "filters": []}
    title = element.find("layout-options/title")
    if title is not None:
        put(ws, "title", text_of(title))
    if name in hidden:
        ws["hidden"] = True
    if view is not None:
        ws["datasources"] = [d.get("name", "") for d in view.findall("datasources/datasource")]
        deps: dict[str, list[str]] = {}
        instances = []
        for block in view.findall("datasource-dependencies"):
            ds_name = block.get("datasource", "")
            deps[ds_name] = [c.get("name", "") for c in block.findall("column")]
            instances += [_instance(ds_name, inst) for inst in block.findall("column-instance")]
        put(ws, "dependencies", deps)
        put(ws, "fieldInstances", instances)
        filters = [typed_filter(f) for f in view.findall("filter")]
        ws["filters"] = [f for f in filters if f]
        put(ws, "sorts", sorts(view))
        aggregation = view.find("aggregation")
        if aggregation is not None and aggregation.get("value") in {"true", "false"}:
            ws["aggregated"] = aggregation.get("value") == "true"
        put(ws, "slices", [text_of(c) for c in view.findall("slices/column")])
    table = element.find("table")
    if table is not None:
        for key in ("rows", "cols", "pages"):
            put(ws, key, shelf(table.find(key), notes, f"{path}/{key}"))
        ws["panes"] = [_pane(p) for p in table.findall("panes/pane")]
        put(ws, "totals", _totals(table))
        rows = table.find("rows")
        if rows is not None and flag(rows, "include-empty"):
            ws["showEmptyRows"] = True
    for pane in ws["panes"]:
        if pane["markClass"] == "VizExtension":
            notes.append(
                {"level": "unsupported", "path": path,
                 "message": "viz extension mark: needs a custom TabFree component"}
            )  # fmt: skip
            break
    return ws
