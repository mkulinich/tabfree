"""Parse Tableau workbooks and data sources (.twb/.twbx/.tds/.tdsx) into the Tableau IR.

The IR (``tabfree/tableau-ir@1``, see packages/schemas) keeps Tableau's own names and encodings
and adds structure (parsed field references and shelves, typed filters, sets, table calcs...) so
that the semantic-model generator and the query planner can work from the IR alone (BUILD_PLAN
P1.8 / P2.1). Files are untrusted: XML is parsed with defusedxml, packages are read in memory with
a size cap, nothing is executed, and credentials (password/token/secret attributes) are never copied.

The work is split by area: :mod:`tabfree_migrate.fieldref` (reference and shelf grammar),
:mod:`tabfree_migrate.twb_datasource`, :mod:`tabfree_migrate.twb_sheets` and
:mod:`tabfree_migrate.twb_layout`; this module loads and normalises the XML and assembles the IR.
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
from tabfree_migrate.fieldref import FieldRefError, parse_field_ref, parse_shelf
from tabfree_migrate.twb_datasource import blend_relationships, datasource
from tabfree_migrate.twb_layout import action, dashboard, resolve_targets, story
from tabfree_migrate.twb_sheets import worksheet
from tabfree_migrate.twb_xml import put

__all__ = [
    "IR_SCHEMA",
    "FieldRefError",
    "TableauFileError",
    "load_root",
    "normalize",
    "parse_field_ref",
    "parse_file",
    "parse_shelf",
    "tableau_version",
]

IR_SCHEMA = "tabfree/tableau-ir@1"
MAX_MEMBER_BYTES = 256 * 1024 * 1024
_FCP = re.compile(r"^_\.fcp\.[A-Za-z0-9_]+\.(true|false)\.\.\.(.+)$")
_BUILD = re.compile(r"^(\d+\.\d+(?:\.\d+)?)")
_ACTION_TAGS = ("action", "nav-action", "edit-group-action", "edit-parameter-action")


class TableauFileError(ValueError):
    """The file is not a readable Tableau workbook or data source."""


# --- loading -----------------------------------------------------------------------------------------


def _unflag(name: str) -> tuple[str, bool]:
    """Strip (possibly nested) ``_.fcp.Feature.true...`` prefixes; True if any prefix is ``false``."""
    legacy = False
    while match := _FCP.match(name):
        legacy = legacy or match.group(1) == "false"
        name = match.group(2)
    return name, legacy


def normalize(element: Element) -> None:
    """Resolve Tableau's feature-flag names (``_.fcp.Flag.true...relation``) in place.

    Tableau writes both variants of flagged content: ``true...`` for the feature on (current Tableau)
    and ``false...`` for older readers. A ``false`` element or attribute is dropped when a sibling
    with the same base name is not a ``false`` variant; everything else is renamed to its base name.
    """
    for node in element.iter():
        if node.attrib and any(k.startswith("_.fcp.") for k in node.attrib):
            resolved: dict[str, tuple[str, bool]] = {}
            for key, value in node.attrib.items():
                base, legacy = _unflag(key)
                previous = resolved.get(base)
                if previous is None or not legacy or previous[1]:
                    resolved[base] = (value, legacy)
            node.attrib.clear()
            node.attrib.update({k: v for k, (v, _legacy) in resolved.items()})
        children = list(node)
        if not any(c.tag.startswith("_.fcp.") for c in children):
            continue
        current = {_unflag(c.tag)[0] for c in children if not _unflag(c.tag)[1]}
        for child in children:
            base, legacy = _unflag(child.tag)
            if legacy and base in current:
                node.remove(child)
            else:
                child.tag = base


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


def parse_xml(data: bytes, name: str) -> Element:
    """Parse untrusted XML with entities and external references forbidden."""
    try:
        root: Element = SafeElementTree.fromstring(data, forbid_entities=True, forbid_external=True)
    except Exception as exc:  # defusedxml and expat raise several unrelated types
        raise TableauFileError(f"{name}: {type(exc).__name__}: {exc}") from None
    return root


def load_root(path: Path, *, normalise: bool = True) -> tuple[Element, bytes, bool]:
    data, packaged = _read_document(path)
    root = parse_xml(data, path.name)
    if normalise:
        normalize(root)
    if _unflag(root.tag)[0] not in {"workbook", "datasource"}:
        raise TableauFileError(f"{path.name}: unexpected root element <{root.tag}>")
    return root, data, packaged


def tableau_version(root: Element) -> str | None:
    """The Tableau release that wrote the file: ``source-build`` first, else ``version``.

    ``version='18.1'`` has been written by every release since 2018.1, so it is no version signal.
    """
    build = root.get("source-build") or ""
    if (match := _BUILD.match(build)) and not match.group(1).startswith("0.0"):
        return match.group(1)
    version = root.get("version") or ""
    if not re.fullmatch(r"\d+\.\d+", version) or version == "18.1":
        return None
    major, minor = version.split(".")
    return f"20{major}.{minor}" if int(major) >= 19 else version


# --- entry point -------------------------------------------------------------------------------------


def _source(path: Path, root: Element, packaged: bool, tableau: dict[str, Any] | None) -> dict[str, Any]:
    source: dict[str, Any] = {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "packaged": packaged,
        "parser": f"tabfree-migrate {__version__}",
        "parsedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    put(source, "sourceBuild", root.get("source-build"))
    put(source, "sourcePlatform", root.get("source-platform"))
    put(source, "version", root.get("version"))
    put(source, "originalVersion", root.get("original-version"))
    put(source, "tableauVersion", tableau_version(root))
    if tableau:
        source["tableau"] = tableau
    return source


def parse_file(path: Path, *, tableau: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a Tableau file into an IR document (validate it with tabfree_schemas.validate)."""
    root, _data, packaged = load_root(path)
    source = _source(path, root, packaged, tableau)
    notes: list[dict[str, Any]] = []
    if root.tag == "datasource":
        return {
            "schema": IR_SCHEMA,
            "kind": "datasource",
            "source": source,
            "datasources": [datasource(root)],
            "notes": notes,
        }

    datasources = [datasource(d) for d in root.findall("datasources/datasource")]
    parameters: list[dict[str, Any]] = next(
        (d["columns"] for d in datasources if d["kind"] == "parameters"), []
    )
    hidden = {
        w.get("name", "")
        for w in root.findall("windows/window")
        if w.get("hidden") == "true" or w.get("class") == "hidden-worksheet"
    }
    worksheets = [worksheet(w, hidden, notes) for w in root.findall("worksheets/worksheet")]
    dashboards, stories = [], []
    for element in root.findall("dashboards/dashboard"):
        if element.get("type") == "storyboard":
            stories.append(story(element))
        else:
            dashboards.append(dashboard(element))
    actions = [action(a) for tag in _ACTION_TAGS for a in root.findall(f"actions/{tag}")]
    sheet_names = {w["name"] for w in worksheets}
    resolve_targets(actions, {d["name"] for d in dashboards} | {s["name"] for s in stories}, sheet_names)
    for act in actions:
        if act["type"] == "unknown":
            notes.append(
                {"level": "unsupported", "path": f"actions/{act['name']}",
                 "message": f"unrecognised action command {act.get('command', '')!r}"}
            )  # fmt: skip
    ir: dict[str, Any] = {
        "schema": IR_SCHEMA,
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
    put(ir, "blendRelationships", blend_relationships(root))
    return ir
