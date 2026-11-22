"""Validate a dashboard bundle: ``tabfree.json`` plus the ``<tf-*>`` references in ``index.html``.

ADR-002 makes the component API a contract: every ``<tf-*>`` element must name a query, filter or
parameter the manifest declares, and every column attribute must be an output of that query. Custom
JavaScript is allowed but flagged for review, with a static scan for the APIs that could move data
out of the sandbox (navigation, network, eval). The scan is a reviewer's aid, not a sandbox.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from tabfree_schemas.validate import SchemaKind, check_manifest_references, schema_errors

#: Element → attribute → what it must name. "query" attributes name queries; "column" attributes name
#: outputs of the element's query; "filter"/"parameter" name declarations.
COMPONENTS: dict[str, dict[str, str]] = {
    "tf-dashboard": {},
    "tf-chart": {
        "query": "query",
        "x": "column",
        "y": "column",
        "color": "column",
        "drill": "query",
        "size": "column",
        "target": "column",
        "tooltip-query": "query",
    },
    "tf-crosstab": {"query": "query", "columns": "column"},
    "tf-kpi": {"query": "query", "measure": "column", "change": "column"},
    "tf-map": {"query": "query", "geo": "column", "color": "column"},
    "tf-filter": {"name": "filter"},
    "tf-parameter": {"name": "parameter"},
    # Layout and content objects (P3.6).
    "tf-container": {},
    "tf-float": {},
    "tf-legend": {"query": "query", "field": "column"},
    "tf-text": {"query": "query"},
    "tf-image": {},
    "tf-export": {"query": "query"},
    # P6.11: dynamic zones, show/hide and navigation buttons.
    "tf-when": {"parameter": "parameter"},
    "tf-toggle": {},
    "tf-nav": {},
}
REQUIRED: dict[str, tuple[str, ...]] = {
    "tf-chart": ("query", "x", "y"),
    "tf-crosstab": ("query",),
    "tf-kpi": ("query", "measure"),
    "tf-map": ("query", "geo", "color"),
    "tf-filter": ("name",),
    "tf-parameter": ("name",),
    "tf-legend": ("query", "field"),
    "tf-image": ("src",),
    "tf-when": ("parameter",),
    "tf-toggle": ("target",),
    "tf-nav": ("dashboard",),
}
ENUMS = {
    ("tf-chart", "mark"): {
        "bar",
        "line",
        "area",
        "point",
        "pie",
        "heatmap",
        "histogram",
        "treemap",
        "boxplot",
        "gantt",
        "bullet",
    },
    ("tf-chart", "trend"): {"linear"},
    ("tf-dashboard", "phone"): {"stack"},
    ("tf-filter", "control"): {
        "dropdown",
        "radio",
        "slider",
        "checklist",
        "multi-dropdown",
        "search",
        "date",
        # Older names, still accepted (relative-date and wildcard filters pick their own control).
        "relative-date",
        "range",
        "wildcard",
    },
    ("tf-dashboard", "size"): {"automatic", "fixed"},
    ("tf-container", "direction"): {"horizontal", "vertical"},
    ("tf-chart", "sort"): {"asc", "desc"},
    ("tf-image", "fit"): {"contain", "cover", "fill"},
    ("tf-export", "format"): {"csv", "crosstab", "png", "pdf"},
    ("tf-parameter", "control"): {"slider", "dropdown", "input"},
}

#: Patterns a reviewer must look at in custom JS (navigation and network are how a frame leaks data).
RISKY_JS = {
    "navigation": re.compile(r"\b(location|window\.open|document\.location|top\.location)\b|\.href\s*="),
    "network": re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts)\b"),
    "dynamic code": re.compile(r"\b(eval|Function)\s*\(|setTimeout\s*\(\s*['\"]"),
    "storage": re.compile(r"\b(localStorage|sessionStorage|indexedDB|document\.cookie)\b"),
}


@dataclass
class BundleReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    components: int = 0
    custom_js: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str], int]] = []
        self.scripts: list[tuple[dict[str, str], str]] = []
        self.external: list[str] = []
        self._script: dict[str, str] | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag.startswith("tf-"):
            self.elements.append((tag, values, self.getpos()[0]))
        if tag == "script":
            self._script = values
            self._buffer = []
            if values.get("src"):
                self.external.append(f"<script src> at line {self.getpos()[0]}")
        if tag in ("link", "img", "iframe", "object", "embed", "form") and any(
            k in values and re.match(r"^\s*(https?:)?//", values[k])
            for k in ("href", "src", "action", "data")
        ):
            self.external.append(f"<{tag}> loading a remote URL at line {self.getpos()[0]}")

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append((self._script, "".join(self._buffer)))
            self._script = None


def query_outputs(query: Mapping[str, Any]) -> set[str]:
    """Output column names of a manifest query (dimensions, measures, table calcs)."""
    names: set[str] = set()
    for item in [*query.get("dimensions", []), *query.get("measures", [])]:
        names.add(item if isinstance(item, str) else str(item.get("as") or item.get("field")))
    names.update(str(c["name"]) for c in query.get("tableCalcs", []))
    for blend in query.get("blend", []):
        names.update(
            item if isinstance(item, str) else str(item.get("as") or item.get("field"))
            for item in blend.get("measures", [])
        )
    if query.get("pivotMeasures"):
        names.update({"Measure Names", "Measure Values"})
    return names


def check_bundle(manifest: Mapping[str, Any], html: str) -> BundleReport:
    report = BundleReport()
    report.errors += [f"tabfree.json: {e}" for e in schema_errors(SchemaKind.DASHBOARD_MANIFEST, manifest)]
    if report.errors:
        return report
    report.errors += [f"tabfree.json: {e}" for e in check_manifest_references(manifest)]
    queries: Mapping[str, Any] = manifest.get("queries", {})
    declared = {
        "query": set(queries),
        "filter": set(manifest.get("filters", {})),
        "parameter": set(manifest.get("parameters", {})),
    }

    parser = _Collector()
    parser.feed(html)
    parser.close()
    for tag, attrs, line in parser.elements:
        where = f"index.html:{line} <{tag}>"
        spec = COMPONENTS.get(tag)
        if spec is None:
            report.errors.append(f"{where}: unknown component")
            continue
        report.components += 1
        for name in REQUIRED.get(tag, ()):
            if not attrs.get(name):
                report.errors.append(f'{where}: missing the "{name}" attribute')
        for (etag, attr), allowed in ENUMS.items():
            if tag == etag and attr in attrs and attrs[attr] not in allowed:
                report.errors.append(f"{where}: {attr}={attrs[attr]!r} is not one of {sorted(allowed)}")
        query = queries.get(attrs.get("query", ""))
        for attr, kind in spec.items():
            value = attrs.get(attr)
            if not value:
                continue
            if kind == "column":
                if query is not None and value not in query_outputs(query):
                    report.errors.append(
                        f'{where}: {attr}="{value}" is not an output of query "{attrs["query"]}"'
                    )
            elif value not in declared[kind]:
                report.errors.append(f'{where}: {attr}="{value}" is not a {kind} declared in tabfree.json')
    for i, action in enumerate(manifest.get("actions", [])):
        sources = list(queries) if action.get("source") == "all" else action.get("source", [])
        for mapping in action.get("fields", []):
            source_field = mapping if isinstance(mapping, str) else mapping.get("source")
            if not any(source_field in query_outputs(queries[q]) for q in sources if q in queries):
                report.errors.append(
                    f"tabfree.json: actions[{i}].fields: {source_field!r} is not an output of the source "
                    "query, so a selection never carries it"
                )
    for item in parser.external:
        report.errors.append(f"index.html: {item} (dashboards cannot load anything from the network)")
    for attrs, code in parser.scripts:
        if attrs.get("type", "") in ("application/json", "application/ld+json"):
            continue
        if not code.strip():
            continue
        report.custom_js = True
        hits = sorted(label for label, pattern in RISKY_JS.items() if pattern.search(code))
        detail = f" (uses {', '.join(hits)})" if hits else ""
        report.warnings.append(
            f"index.html: custom JavaScript needs a human review before publishing{detail}"
        )
    if report.components == 0 and not report.custom_js:
        report.warnings.append("index.html: no <tf-*> components and no script: the dashboard shows no data")
    return report


def check_bundle_dir(folder: Path) -> BundleReport:
    manifest = json.loads((folder / "tabfree.json").read_text(encoding="utf-8"))
    return check_bundle(manifest, (folder / "index.html").read_text(encoding="utf-8"))
