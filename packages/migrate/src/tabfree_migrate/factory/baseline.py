"""The deterministic HTML baseline (BUILD_PLAN P5.3, first stage): a dashboard plan → ``index.html``.

- The **tiled zone tree** becomes nested ``<tf-container>`` elements: a horizontal or vertical flow
  keeps its direction, and each child's share of the space (its ``weight``) is its size in Tableau's
  layout units, so proportions match.
- **Floating zones** become ``<tf-float>`` at their pixel position on the dashboard's size.
- **Worksheets** become ``<tf-chart>`` (bar, line, area, point), ``<tf-crosstab>``, ``<tf-kpi>`` or
  ``<tf-map>``, from the mark type and the shelves: columns → x, rows → y, color → color.
- **Filter cards and parameter controls** become ``<tf-filter>`` and ``<tf-parameter>``; colour
  legends ``<tf-legend>``; text objects ``<tf-text>`` (as text); titles a heading; images from a
  packaged workbook ``<tf-image>`` with a ``data:`` URL (PNG, JPEG and GIF only).

What the baseline cannot express (web pages, extensions, unknown marks) gets a visible placeholder and
a line in the plan's ``unsupported`` list, which the Claude repair agent works from. Every value from
the workbook is escaped: text is content, never markup.
"""

from __future__ import annotations

import base64
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any

from tabfree_migrate.factory.manifest import DashboardPlan, SheetPlan
from tabfree_schemas.bundle import query_outputs

UNITS = 100_000
DEFAULT_SIZE = (1000, 800)
MAX_IMAGE_BYTES = 256 * 1024
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
_MARKS = {
    "bar": "bar",
    "ganttbar": "bar",
    "square": "bar",
    "line": "line",
    "area": "area",
    "circle": "point",
    "shape": "point",
}
_FILTER_CONTROLS = {
    "dropdown": "dropdown",
    "checkdropdown": "multi-dropdown",
    "checklist": "checklist",
    "radiolist": "radio",
    "slider": "slider",
    "typein": "search",
}
_LEGENDS = {"color", "size", "shape", "highlighter"}


@dataclass(frozen=True)
class View:
    """What one worksheet becomes."""

    tag: str
    attrs: Mapping[str, str]
    notes: tuple[str, ...] = ()


def _outputs(spec: Mapping[str, Any]) -> list[str]:
    return sorted(query_outputs(spec)) if spec else []


def _first(names: Sequence[str] | None, allowed: set[str]) -> str | None:
    return next((n for n in names or [] if n in allowed), None)


def _dims(spec: Mapping[str, Any]) -> list[str]:
    out = []
    for d in spec.get("dimensions", []) or []:
        out.append(d if isinstance(d, str) else str(d.get("as") or d.get("field")))
    return out


def _measures(spec: Mapping[str, Any]) -> list[str]:
    out = []
    for m in spec.get("measures", []) or []:
        out.append(m if isinstance(m, str) else str(m.get("as") or m.get("field")))
    return out


def view_for(sheet: SheetPlan) -> View:
    """The component for one worksheet (deterministic; the repair agent refines it)."""
    spec = sheet.spec
    if not spec:
        return View("div", {"class": "tf-unsupported"}, ("not converted",))
    outputs = set(_outputs(spec))
    dims = [d for d in _dims(spec) if d in outputs]
    measures = [m for m in _measures(spec) if m in outputs]
    shelves = sheet.report.shelves
    marks = [m.lower() for m in sheet.marks if m.lower() != "automatic"]
    mark = marks[0] if marks else "automatic"
    title = {"title": sheet.worksheet}
    if mark == "multipolygon" or any("geocoded by Tableau" in u for u in sheet.report.unsupported):
        geo = _first(dims, outputs)
        color = _first(shelves.get("color"), outputs) or (measures[0] if measures else None)
        if geo and color:
            return View(
                "tf-map",
                {"query": sheet.query, "geo": geo, "color": color, **title},
                ("map: check the geography",),
            )
        return View("tf-crosstab", {"query": sheet.query, **title}, ("map shown as a table",))
    if not dims and measures:
        measure = _first(shelves.get("text"), outputs) or _first(shelves.get("label"), outputs) or measures[0]
        return View("tf-kpi", {"query": sheet.query, "measure": measure, "label": sheet.worksheet})
    rows = [n for n in shelves.get("rows", []) if n in outputs]
    cols = [n for n in shelves.get("cols", []) if n in outputs]
    on_axes = set(rows) | set(cols)
    if mark == "text" or (measures and not (on_axes & set(measures))) or not measures:
        attrs = {"query": sheet.query, **title}
        if rows and cols and cols[0] in dims:
            attrs["columns"] = cols[0]
        return View("tf-crosstab", attrs)
    x = cols[0] if cols else None
    y = rows[0] if rows else None
    if x is None or y is None:
        x = x or (dims[0] if y in measures else measures[0])
        y = y or (measures[0] if x in dims else dims[0] if dims else measures[0])
    chart_mark = _MARKS.get(mark)
    notes: list[str] = []
    if chart_mark is None:
        temporal = any("TRUNC(" in a or "(" in a for a in (x, y) if a in dims)
        chart_mark = "line" if mark == "automatic" and temporal else "bar"
        if mark not in ("automatic",):
            notes.append(f"{sheet.worksheet}: {mark} marks drawn as {chart_mark}")
    attrs = {"query": sheet.query, "mark": chart_mark, "x": x, "y": y, **title}
    color = _first(shelves.get("color"), outputs)
    if color and color not in (x, y):
        attrs["color"] = color
    if len(rows) + len(cols) > 2:
        notes.append(f"{sheet.worksheet}: more than one field per axis; only the first of each is drawn")
    return View("tf-chart", attrs, tuple(notes))


def _attrs(attrs: Mapping[str, Any]) -> str:
    return "".join(
        f' {k}="{escape(str(v), quote=True)}"' for k, v in attrs.items() if v is not None and v != ""
    )


def _text(text: str) -> str:
    # Tableau separates paragraphs with "Æ" in formatted text; placeholders like <Sheet Name> stay text.
    lines = [line.strip() for line in text.replace("Æ", "\n").splitlines()]
    return escape("\n".join(line for line in lines if line))


class _Renderer:
    def __init__(self, plan: DashboardPlan, package: Path | None) -> None:
        self.plan = plan
        self.package = package
        size = plan.size or {}
        self.width = int(size.get("width") or size.get("maxWidth") or DEFAULT_SIZE[0])
        self.height = int(size.get("height") or size.get("maxHeight") or DEFAULT_SIZE[1])
        self.notes: list[str] = []
        self.unsupported: list[str] = []
        self.extra: dict[str, Any] = {}

    def image(self, member: str) -> str | None:
        if self.package is None or self.package.suffix.lower() not in (".twbx", ".tdsx"):
            return None
        kind = _IMAGE_TYPES.get(PurePosixPath(member).suffix.lower())
        if kind is None:
            return None
        try:
            with zipfile.ZipFile(self.package) as archive:
                wanted = member.replace("\\", "/").lstrip("/").lower()
                info = next((i for i in archive.infolist() if i.filename.lower().endswith(wanted)), None)
                if info is None or info.file_size > MAX_IMAGE_BYTES:
                    return None
                data = archive.read(info)
        except (OSError, zipfile.BadZipFile):
            return None
        return f"data:{kind};base64,{base64.b64encode(data).decode()}"

    def _a(self, attrs: Mapping[str, Any]) -> str:
        return _attrs({**attrs, **self.extra})

    def leaf(self, zone: Mapping[str, Any], indent: str) -> str:
        kind = str(zone.get("type"))
        zid = str(zone.get("id", ""))
        if kind == "viz":
            sheet = self.plan.sheet(str(zone.get("worksheet") or ""))
            if sheet is None:
                return f"{indent}<!-- worksheet {escape(str(zone.get('worksheet')))} -->"
            view = view_for(sheet)
            self.notes += list(view.notes)
            attrs = dict(view.attrs)
            if zone.get("showTitle") is False:
                attrs.pop("title", None)
            if view.tag == "div":
                self.unsupported.append(f"worksheet {sheet.worksheet!r} has no converted query")
                attrs = {"class": "tf-unsupported", "data-worksheet": sheet.worksheet}
                return f"{indent}<div{self._a(attrs)}>{escape(sheet.worksheet)} (not converted)</div>"
            return f"{indent}<{view.tag}{self._a(attrs)}></{view.tag}>"
        if kind == "filter":
            control = self.plan.controls.get(zid)
            if not control:
                return f"{indent}<!-- filter card on {escape(str(zone.get('field')))} (not converted) -->"
            spec = self.plan.manifest.get("filters", {}).get(control[1], {})
            attrs = {"name": control[1]}
            if spec.get("kind") == "categorical":
                attrs["control"] = _FILTER_CONTROLS.get(
                    str(zone.get("mode") or "checkdropdown"), "multi-dropdown"
                )
            return f"{indent}<tf-filter{self._a(attrs)}></tf-filter>"
        if kind == "paramctrl":
            control = self.plan.controls.get(zid)
            if not control:
                return f"{indent}<!-- parameter control (not converted) -->"
            return f"{indent}<tf-parameter{self._a({'name': control[1]})}></tf-parameter>"
        if kind in _LEGENDS:
            sheet = self.plan.sheet(str(zone.get("worksheet") or ""))
            if sheet and sheet.spec:
                view = view_for(sheet)
                color = view.attrs.get("color")
                if kind == "color" and color and color in _dims(sheet.spec):
                    return f"{indent}<tf-legend{self._a({'query': sheet.query, 'field': color})}></tf-legend>"
            self.notes.append(
                f"{kind} legend for {zone.get('worksheet')!r} left out (the chart shows its own)"
            )
            return f"{indent}<!-- {escape(kind)} legend -->"
        if kind == "text":
            return f"{indent}<tf-text{self._a({})}>{_text(str(zone.get('text', '')))}</tf-text>"
        if kind == "title":
            return f"{indent}<h1{self._a({'class': 'tf-title'})}>{escape(self.plan.title)}</h1>"
        if kind == "bitmap":
            src = self.image(str(zone.get("image", "")))
            if src is None:
                self.unsupported.append(f"image {zone.get('image')!r}: not packaged or not PNG/JPEG/GIF")
                return f"{indent}<!-- image {escape(str(zone.get('image', '')))} -->"
            alt = str(zone.get("altText") or PurePosixPath(str(zone.get("image", ""))).stem)
            return f"{indent}<tf-image{self._a({'src': src, 'alt': alt, 'fit': 'contain'})}></tf-image>"
        if kind == "web":
            self.unsupported.append("web page object: dashboards make no network calls")
            return f"{indent}<div{self._a({'class': 'tf-unsupported'})}>Web page (not supported)</div>"
        if kind in ("dashboard-object", "add-in"):
            self.unsupported.append("dashboard extension: not supported")
            return f"{indent}<div{self._a({'class': 'tf-unsupported'})}>Extension (not supported)</div>"
        if kind == "empty":
            return f"{indent}<div{self._a({'class': 'tf-blank'})}></div>"
        self.notes.append(f"zone type {kind!r} left out")
        return f"{indent}<!-- {escape(kind)} -->"

    def tiled(self, zone: Mapping[str, Any], depth: int, parent_direction: str | None) -> str:
        indent = "  " * (depth + 2)
        kind = str(zone.get("type"))
        children = [c for c in zone.get("children", []) or [] if not c.get("hidden")]
        weight = None
        if parent_direction == "horizontal":
            weight = max(1, round(int(zone.get("w") or 0) / 1000))
        elif parent_direction == "vertical":
            weight = max(1, round(int(zone.get("h") or 0) / 1000))
        if kind in ("layout-flow", "layout-basic") and children:
            if kind == "layout-flow":
                direction = (
                    "horizontal" if (zone.get("layout") or {}).get("direction") == "horz" else "vertical"
                )
            else:
                xs = {int(c.get("x") or 0) for c in children}
                direction = "horizontal" if len(xs) == len(children) and len(children) > 1 else "vertical"
                children = sorted(children, key=lambda c: (int(c.get("y") or 0), int(c.get("x") or 0)))
            if len(children) == 1 and weight is None:
                return self.tiled(children[0], depth, parent_direction)
            attrs = {"direction": direction, "weight": weight}
            inner = "\n".join(self.tiled(c, depth + 1, direction) for c in children)
            return f"{indent}<tf-container{_attrs(attrs)}>\n{inner}\n{indent}</tf-container>"
        self.extra = {"weight": weight} if weight is not None else {}
        try:
            return self.leaf(zone, indent)
        finally:
            self.extra = {}

    def floating(self, zone: Mapping[str, Any]) -> str:
        indent = "      "
        box = {
            "x": round(int(zone.get("x") or 0) * self.width / UNITS),
            "y": round(int(zone.get("y") or 0) * self.height / UNITS),
            "width": round(int(zone.get("w") or 0) * self.width / UNITS),
            "height": round(int(zone.get("h") or 0) * self.height / UNITS),
        }
        children = zone.get("children") or []
        inner = (
            "\n".join(self.tiled(c, 2, None) for c in children)
            if children
            else self.leaf(zone, indent + "  ")
        )
        return f"{indent}<tf-float{_attrs(box)}>\n{inner}\n{indent}</tf-float>"


_STYLE = """      tf-dashboard { display: block; padding: 8px; }
      tf-container { gap: 8px; }
      .tf-title { font-size: 1.25rem; margin: 0; }
      .tf-blank { min-height: 4px; }
      .tf-unsupported { border: 1px dashed currentColor; opacity: 0.6; padding: 8px; }"""


def render(plan: DashboardPlan, *, package: Path | None = None) -> str:
    """The baseline ``index.html`` for one plan; notes and gaps are added to the plan."""
    renderer = _Renderer(plan, package)
    size = plan.size or {}
    fixed = size.get("mode") == "fixed" or bool(size.get("width") and size.get("height"))
    dash_attrs: dict[str, Any] = {"layout": "tiled", "size": "fixed" if fixed else "automatic"}
    style = _STYLE
    if fixed:
        style += f"\n      tf-dashboard {{ width: {renderer.width}px; min-height: {renderer.height}px; }}"
    body: list[str] = []
    if plan.zones:
        tiled = [z for z in plan.zones if not z.get("floating")]
        floating = [z for z in plan.zones if z.get("floating") and not z.get("hidden")]
        body += [renderer.tiled(z, 1, None) for z in tiled]
        body += [renderer.floating(z) for z in floating]
    else:  # a worksheet published on its own
        body += [renderer.leaf({"type": "viz", "worksheet": s.worksheet}, "      ") for s in plan.sheets]
    plan.notes += renderer.notes
    plan.unsupported += renderer.unsupported
    title = escape(plan.title)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        f"    <title>{title}</title>\n"
        f"    <!-- Converted from Tableau ({escape(plan.workbook)}) by the TabFree migration factory. -->\n"
        f"    <style>\n{style}\n    </style>\n"
        "  </head>\n"
        "  <body>\n"
        f"    <tf-dashboard{_attrs(dash_attrs)}>\n" + "\n".join(body) + "\n    </tf-dashboard>\n"
        "  </body>\n"
        "</html>\n"
    )


def notes_markdown(plan: DashboardPlan) -> str:
    """``README.md`` for the bundle: where it came from and what to review."""
    lines = [f"# {plan.title}", "", f"Converted from the Tableau workbook *{plan.workbook}*"]
    if plan.dashboard:
        lines[-1] += f", dashboard *{plan.dashboard}*"
    lines[-1] += ", by the TabFree migration factory (deterministic baseline)."
    if plan.unsupported:
        lines += ["", "## Not converted", ""] + [f"- {u}" for u in dict.fromkeys(plan.unsupported)]
    if plan.notes:
        lines += ["", "## Review", ""] + [f"- {n}" for n in dict.fromkeys(plan.notes)]
    return "\n".join(lines) + "\n"
