"""Dashboard bundles as files, and patch-style edits to them.

A bundle is two files: ``tabfree.json`` (the manifest, the data contract) and ``index.html`` (the
dashboard). ``README.md`` may hold the author's notes. Scripts and styles go inline in ``index.html``
and images are ``data:`` URLs, because the dashboard frame loads nothing from the network.

A revision stores the manifest as a document and the other files as text; drafts store all three as
text so an edit can never leave a half-parsed manifest behind.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

MANIFEST = "tabfree.json"
HTML = "index.html"
NOTES = "README.md"
ALLOWED = (MANIFEST, HTML, NOTES)
MAX_FILE_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024
MAX_EDITS = 50


class EditError(ValueError):
    """An edit that cannot apply; the message tells the author (or the agent) what to fix."""


def manifest_text(manifest: Mapping[str, Any]) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def from_revision(document: Mapping[str, Any], files: Mapping[str, str]) -> dict[str, str]:
    out = {MANIFEST: manifest_text(document)}
    out.update({k: str(v) for k, v in files.items() if k in ALLOWED and k != MANIFEST})
    out.setdefault(HTML, "")
    return out


def parse_manifest(files: Mapping[str, str]) -> dict[str, Any]:
    try:
        manifest = json.loads(files.get(MANIFEST, ""))
    except json.JSONDecodeError as exc:
        raise EditError(f"{MANIFEST} is not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(manifest, dict):
        raise EditError(f"{MANIFEST} must be a JSON object")
    return manifest


def to_revision(files: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """The manifest document and the other files, as ``DbSite.publish`` takes them."""
    return parse_manifest(files), {k: v for k, v in files.items() if k != MANIFEST}


def files_hash(files: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(files), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_limits(files: Mapping[str, str]) -> None:
    unknown = sorted(set(files) - set(ALLOWED))
    if unknown:
        raise EditError(
            f"bundles contain only {', '.join(ALLOWED)}; inline scripts and styles in {HTML} "
            f"(not {', '.join(unknown)})"
        )
    total = 0
    for path, text in files.items():
        size = len(text.encode())
        if size > MAX_FILE_BYTES:
            raise EditError(f"{path} is {size} bytes; files are limited to {MAX_FILE_BYTES}")
        total += size
    if total > MAX_BUNDLE_BYTES:
        raise EditError(f"the bundle is {total} bytes; bundles are limited to {MAX_BUNDLE_BYTES}")


# ---- edits ------------------------------------------------------------------------------------------

EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "op"],
    "properties": {
        "path": {"type": "string", "enum": list(ALLOWED)},
        "op": {
            "type": "string",
            "enum": ["write", "replace", "delete", "json_set", "json_remove"],
            "description": (
                "write: replace the whole file with `content`. replace: change the exact text `old` "
                "(which must occur once, or set `all`) to `new`. delete: remove README.md. json_set / "
                "json_remove: set or remove the value at `pointer` (RFC 6901, e.g. "
                "/queries/by_region/sort) in tabfree.json."
            ),
        },
        "content": {"type": "string"},
        "old": {"type": "string"},
        "new": {"type": "string"},
        "all": {"type": "boolean"},
        "pointer": {"type": "string"},
        "value": {},
    },
}


def apply_edits(files: Mapping[str, str], edits: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """The files after every edit, in order; nothing changes if any edit fails."""
    if not edits:
        raise EditError("no edits given")
    if len(edits) > MAX_EDITS:
        raise EditError(f"at most {MAX_EDITS} edits per call")
    out = dict(files)
    for i, edit in enumerate(edits):
        where = f"edit {i + 1} ({edit.get('op')} {edit.get('path')})"
        path = edit.get("path")
        if path not in ALLOWED:
            raise EditError(f"{where}: unknown file; bundles contain only {', '.join(ALLOWED)}")
        op = edit.get("op")
        if op == "write":
            content = edit.get("content")
            if not isinstance(content, str):
                raise EditError(f"{where}: needs `content`")
            out[path] = content
        elif op == "replace":
            old, new = edit.get("old"), edit.get("new")
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise EditError(f"{where}: needs `old` (not empty) and `new`")
            text = out.get(path, "")
            count = text.count(old)
            if count == 0:
                raise EditError(
                    f"{where}: `old` does not occur in {path}; get the file again and copy it exactly"
                )
            if count > 1 and not edit.get("all"):
                raise EditError(f"{where}: `old` occurs {count} times; add context or set `all`")
            out[path] = text.replace(old, new)
        elif op == "delete":
            if path != NOTES:
                raise EditError(f"{where}: only {NOTES} can be deleted")
            out.pop(path, None)
        elif op in ("json_set", "json_remove"):
            if path != MANIFEST:
                raise EditError(f"{where}: JSON edits apply to {MANIFEST} only")
            pointer = edit.get("pointer")
            if not isinstance(pointer, str):
                raise EditError(f"{where}: needs `pointer`")
            try:
                manifest = parse_manifest(out)
            except EditError as exc:
                raise EditError(f"{where}: {exc}") from None
            if op == "json_set":
                if "value" not in edit:
                    raise EditError(f"{where}: needs `value`")
                manifest = _pointer_set(manifest, pointer, edit["value"], where)
            else:
                manifest = _pointer_remove(manifest, pointer, where)
            out[MANIFEST] = manifest_text(manifest)
        else:
            raise EditError(f"{where}: unknown op")
    check_limits(out)
    return out


def _tokens(pointer: str, where: str) -> list[str]:
    if not pointer.startswith("/"):
        raise EditError(f"{where}: a pointer starts with '/', e.g. /queries/by_region")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _walk(doc: Any, tokens: list[str], where: str) -> Any:
    node = doc
    for token in tokens:
        if isinstance(node, dict):
            if token not in node:
                raise EditError(f"{where}: no {token!r} in the manifest at that path")
            node = node[token]
        elif isinstance(node, list):
            node = node[_index(node, token, where, allow_end=False)]
        else:
            raise EditError(f"{where}: {token!r} is inside a value that is not an object or array")
    return node


def _index(items: list[Any], token: str, where: str, *, allow_end: bool) -> int:
    if token == "-" and allow_end:  # noqa: S105 - RFC 6901's "past the end" marker
        return len(items)
    if not token.isdigit():
        raise EditError(f"{where}: {token!r} is not an array index")
    index = int(token)
    if index > len(items) or (index == len(items) and not allow_end):
        raise EditError(f"{where}: index {index} is out of range")
    return index


def _pointer_set(doc: dict[str, Any], pointer: str, value: Any, where: str) -> dict[str, Any]:
    tokens = _tokens(pointer, where)
    doc = copy.deepcopy(doc)
    parent = _walk(doc, tokens[:-1], where)
    last = tokens[-1]
    if isinstance(parent, dict):
        parent[last] = value
    elif isinstance(parent, list):
        index = _index(parent, last, where, allow_end=True)
        if index == len(parent):
            parent.append(value)
        else:
            parent[index] = value
    else:
        raise EditError(f"{where}: the parent of {pointer} is not an object or array")
    return doc


def _pointer_remove(doc: dict[str, Any], pointer: str, where: str) -> dict[str, Any]:
    tokens = _tokens(pointer, where)
    doc = copy.deepcopy(doc)
    parent = _walk(doc, tokens[:-1], where)
    last = tokens[-1]
    if isinstance(parent, dict) and last in parent:
        del parent[last]
    elif isinstance(parent, list):
        del parent[_index(parent, last, where, allow_end=False)]
    else:
        raise EditError(f"{where}: nothing at {pointer}")
    return doc


# ---- diffs ------------------------------------------------------------------------------------------


def diff(before: Mapping[str, str], after: Mapping[str, str], *, labels: tuple[str, str] = ("a", "b")) -> str:
    """A unified diff of two bundles, file by file."""
    chunks: list[str] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        chunks.extend(
            difflib.unified_diff(
                (old or "").splitlines(keepends=True),
                (new or "").splitlines(keepends=True),
                fromfile=f"{labels[0]}/{path}" if old is not None else "/dev/null",
                tofile=f"{labels[1]}/{path}" if new is not None else "/dev/null",
            )
        )
    return "".join(line if line.endswith("\n") else line + "\n" for line in chunks)


def diff_stat(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for line_group in _per_file(before, after):
        path, added, removed = line_group
        out[path] = {"added": added, "removed": removed}
    return out


def _per_file(before: Mapping[str, str], after: Mapping[str, str]) -> list[tuple[str, int, int]]:
    result = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path, ""), after.get(path, "")
        if old == new:
            continue
        added = removed = 0
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        result.append((path, added, removed))
    return result


def starter(dashboard: str, title: str, model: str, model_doc: Mapping[str, Any]) -> dict[str, str]:
    """A new dashboard on ``model``: one query and one component, for the author to build on."""
    fields = [f for f in model_doc.get("fields", []) if not f.get("hidden")]
    measure = next((f["name"] for f in fields if f.get("role") == "measure"), None)
    dimension = next((f["name"] for f in fields if f.get("role") == "dimension"), None)
    if measure is not None:
        query: dict[str, Any] = {"measures": [measure]}
        component = f'<tf-kpi query="total" measure="{_escape(measure)}"></tf-kpi>'
    elif dimension is not None:
        query = {"dimensions": [dimension], "limit": 100}
        component = '<tf-crosstab query="total"></tf-crosstab>'
    else:
        raise EditError(f"model {model!r} has no fields to start from")
    manifest = {
        "schema": "tabfree/dashboard@0",
        "id": dashboard,
        "title": title,
        "models": {"data": model},
        "queries": {"total": query},
    }
    html = f"<tf-dashboard>\n  <h1>{_escape(title)}</h1>\n  {component}\n</tf-dashboard>\n"
    return {MANIFEST: manifest_text(manifest), HTML: html}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
