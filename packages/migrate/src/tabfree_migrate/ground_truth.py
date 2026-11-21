"""Ground-truth capture per view (BUILD_PLAN P1.5): the evidence that disappears with Tableau.

For every in-scope view this captures, through the REST API's view exports:

- a high-resolution PNG at a fixed ``vizWidth``/``vizHeight`` (``/views/<luid>/image``)
- a PDF (``/views/<luid>/pdf``)
- the summary data as CSV (``/views/<luid>/data``, what *View Data → Summary* shows)
- the crosstab as Excel (``/views/<luid>/crosstab/excel``; TSC ``populate_excel``)

and, for the top views by usage, the same exports under filter and parameter permutations, sent as
``vf_<field>=<value>`` and ``vf_Parameters.<name>=<value>``. Permutations come from a JSON file
(see :func:`load_permutations`) because sensible values depend on each workbook.

Each capture is stored content-addressed (sha256) in the sink, and ``ground_truth/index.json``
records the view, the workbook ``updated_at``, the exact export options **and the query parameters
they produce**, hash, size and time, so a capture can be repeated and compared later. Re-runs skip
captures that already succeeded for an unchanged workbook.

All calls are read-only GETs. **Unverified** against a live server: in particular how a given
server version treats ``vf_`` names with spaces or dots, and crosstab export availability (REST
3.8+), must be checked on the first real run.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from tabfree_migrate.blobstore import BinaryWriter, HashingWriter, Sink, load_json, save_json, store_stream
from tabfree_migrate.inventory import InventoryStore
from tabfree_migrate.retry import with_retries

INDEX_KEY = "ground_truth/index.json"
FORMATS = ("png", "pdf", "csv", "xlsx")
PERMUTATION_FORMATS = ("png", "csv")

_EXTENSIONS = {"png": ".png", "pdf": ".pdf", "csv": ".csv", "xlsx": ".xlsx"}
_PATHS = {"png": "image", "pdf": "pdf", "csv": "data", "xlsx": "crosstab/excel"}


@dataclass(frozen=True)
class ExportOptions:
    """Everything that changes an export's bytes, recorded with every capture."""

    viz_width: int = 1920
    viz_height: int = 1080
    resolution: str | None = "high"
    max_age: int | None = None
    page_type: str | None = None
    orientation: str | None = None
    filters: tuple[tuple[str, str], ...] = ()
    parameters: tuple[tuple[str, str], ...] = ()

    def query_params(self, fmt: str) -> dict[str, str]:
        """The query string this export sends (mirrors TSC's request options)."""
        if fmt not in FORMATS:
            raise ValueError(f"unknown export format {fmt!r}")
        params: dict[str, str] = {}
        if self.max_age is not None:
            params["maxAge"] = str(self.max_age)
        for name, value in self.filters:
            params[f"vf_{name}"] = value
        for name, value in self.parameters:
            params[parameter_key(name)] = value
        if fmt in {"png", "pdf"}:
            params["vizWidth"] = str(self.viz_width)
            params["vizHeight"] = str(self.viz_height)
        if fmt == "png" and self.resolution:
            params["resolution"] = self.resolution
        if fmt == "pdf":
            if self.page_type:
                params["type"] = self.page_type
            if self.orientation:
                params["orientation"] = self.orientation
        return params

    def with_permutation(self, permutation: Permutation) -> ExportOptions:
        return replace(
            self,
            filters=tuple(sorted(permutation.filters.items())),
            parameters=tuple(sorted(permutation.parameters.items())),
        )


def parameter_key(name: str) -> str:
    """``vf_Parameters.<name>`` the way TSC's ``RequestOptions.parameter`` spells it."""
    if name.startswith("vf_Parameters."):
        return name
    if name.startswith("Parameters."):
        return f"vf_{name}"
    return f"vf_Parameters.{name}"


@dataclass(frozen=True)
class Permutation:
    label: str
    filters: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, str] = field(default_factory=dict)


def load_permutations(document: Mapping[str, Any]) -> dict[str, list[Permutation]]:
    """Parse ``{"default": [...], "views": {"<view luid>": [...]}}``.

    Each entry is ``{"label": "east", "filters": {"Region": "East"}, "parameters": {"Top N": "5"}}``.
    ``default`` applies to every top view that has no entry of its own. Values must be strings.
    """

    def parse(entries: Any) -> list[Permutation]:
        if not isinstance(entries, list):
            raise ValueError("permutations must be lists")
        result = []
        for i, entry in enumerate(entries):
            filters = entry.get("filters") or {}
            parameters = entry.get("parameters") or {}
            for name, value in [*filters.items(), *parameters.items()]:
                if not isinstance(name, str) or not isinstance(value, str):
                    raise ValueError(f"permutation {i}: names and values must be strings")
            result.append(Permutation(str(entry.get("label") or f"p{i}"), dict(filters), dict(parameters)))
        return result

    out = {"*": parse(document.get("default") or [])}
    for luid, entries in (document.get("views") or {}).items():
        out[str(luid)] = parse(entries)
    return out


@dataclass(frozen=True)
class ViewTarget:
    luid: str
    name: str
    workbook_luid: str | None
    workbook_updated_at: str | None
    usage: int = 0


def targets_from_inventory(
    inventory: InventoryStore, usage: Mapping[str, int] | None = None
) -> list[ViewTarget]:
    """Views from the P1.1 crawl, most used first. ``usage`` maps view LUID → view count.

    Without ``usage``, the REST ``total_views`` stored by the crawl (when present) is used.
    """
    workbooks = {item.luid: item.updated_at for item in inventory.objects("workbooks")}
    targets = []
    for item in inventory.objects("views"):
        workbook = item.data.get("workbook_id")
        count = usage.get(item.luid, 0) if usage is not None else int(item.data.get("total_views") or 0)
        targets.append(
            ViewTarget(
                item.luid, item.name, workbook, workbooks.get(str(workbook)) if workbook else None, count
            )
        )
    return sorted(targets, key=lambda t: (-t.usage, t.name, t.luid))


class ViewExporter(Protocol):
    """Streams one view export into ``out``. TSC-backed; faked in tests."""

    def export(self, view_luid: str, fmt: str, options: ExportOptions, out: BinaryWriter) -> None: ...


@dataclass
class CaptureStats:
    captured: int = 0
    skipped: int = 0
    bytes: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def capture_id(view_luid: str, fmt: str, options: ExportOptions) -> str:
    digest = hashlib.sha256(
        json.dumps({"format": fmt, "params": options.query_params(fmt)}, sort_keys=True).encode()
    ).hexdigest()
    return f"{view_luid}/{fmt}/{digest[:16]}"


def plan_captures(
    targets: Iterable[ViewTarget],
    base: ExportOptions,
    formats: Iterable[str] = FORMATS,
    permutations: Mapping[str, list[Permutation]] | None = None,
    top: int = 25,
    permutation_formats: Iterable[str] = PERMUTATION_FORMATS,
    max_permutations: int = 20,
) -> list[tuple[ViewTarget, str, str, ExportOptions]]:
    """``(view, format, label, options)`` for every capture: base exports for all views, then
    permutations for the ``top`` views (targets are expected most-used first)."""
    formats = tuple(formats)
    permutation_formats = tuple(permutation_formats)
    targets = list(targets)
    plan = [(t, fmt, "base", base) for t in targets for fmt in formats]
    if permutations:
        for target in targets[:top]:
            chosen = permutations.get(target.luid, permutations.get("*", []))[:max_permutations]
            for perm in chosen:
                options = base.with_permutation(perm)
                plan += [(target, fmt, perm.label, options) for fmt in permutation_formats]
    return plan


def capture_all(
    exporter: ViewExporter,
    sink: Sink,
    plan: Iterable[tuple[ViewTarget, str, str, ExportOptions]],
    force: bool = False,
    save_every: int = 25,
    staging: Path | None = None,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> CaptureStats:
    """Run the plan into ``sink`` and keep ``ground_truth/index.json`` up to date.

    A failed capture is recorded in the index and the next one runs.
    """
    index = load_json(sink, INDEX_KEY)
    captures: dict[str, Any] = index.setdefault("captures", {})
    stats = CaptureStats()
    pending = 0
    try:
        for target, fmt, label, options in plan:
            cid = capture_id(target.luid, fmt, options)
            previous = captures.get(cid)
            if (
                not force
                and previous
                and not previous.get("error")
                and previous.get("workbook_updated_at") == target.workbook_updated_at
                and sink.exists(str(previous.get("key")))
            ):
                stats.skipped += 1
                continue
            entry: dict[str, Any] = {
                "view_luid": target.luid,
                "view_name": target.name,
                "workbook_luid": target.workbook_luid,
                "workbook_updated_at": target.workbook_updated_at,
                "usage": target.usage,
                "format": fmt,
                "label": label,
                "options": asdict(options),
                "request": {
                    "path": f"views/{target.luid}/{_PATHS[fmt]}",
                    "params": options.query_params(fmt),
                },
                "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            fill = _filler(exporter, target.luid, fmt, options)
            ext = _EXTENSIONS[fmt]
            try:
                ref = with_retries(
                    partial(store_stream, sink, fill, ext, staging),
                    attempts=attempts,
                    sleep=sleep,
                )
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
                stats.errors[cid] = entry["error"]
            else:
                entry.update(sha256=ref.sha256, size=ref.size, key=ref.key, content_type=ref.content_type)
                stats.captured += 1
                stats.bytes += ref.size
            captures[cid] = entry
            pending += 1
            if pending >= save_every:
                save_json(sink, INDEX_KEY, index)
                pending = 0
    finally:
        save_json(sink, INDEX_KEY, index)
    return stats


def _filler(
    exporter: ViewExporter, luid: str, fmt: str, options: ExportOptions
) -> Callable[[HashingWriter], None]:
    def fill(writer: HashingWriter) -> None:
        exporter.export(luid, fmt, options, writer)
        if writer.size == 0:
            raise ValueError("empty export")

    return fill


class TscViewExporter:
    """:class:`ViewExporter` over TSC's ``views.populate_image/pdf/csv/excel`` (read-only GETs)."""

    def __init__(self, tsc_server: Any, tsc_module: Any) -> None:
        self._srv = tsc_server
        self._tsc = tsc_module

    def _request_options(self, fmt: str, options: ExportOptions) -> Any:
        tsc = self._tsc
        max_age = -1 if options.max_age is None else options.max_age
        if fmt == "png":
            req = tsc.ImageRequestOptions(
                imageresolution=options.resolution,
                maxage=max_age,
                viz_height=options.viz_height,
                viz_width=options.viz_width,
            )
        elif fmt == "pdf":
            req = tsc.PDFRequestOptions(
                page_type=options.page_type,
                orientation=options.orientation,
                maxage=max_age,
                viz_height=options.viz_height,
                viz_width=options.viz_width,
            )
        elif fmt == "csv":
            req = tsc.CSVRequestOptions(maxage=max_age)
        elif fmt == "xlsx":
            req = tsc.ExcelRequestOptions(maxage=max_age)
        else:
            raise ValueError(f"unknown export format {fmt!r}")
        for name, value in options.filters:
            req.vf(name, value)
        for name, value in options.parameters:
            req.parameter(name, value)
        return req

    def export(self, view_luid: str, fmt: str, options: ExportOptions, out: BinaryWriter) -> None:
        views = self._srv.views
        view = views.get_by_id(view_luid)
        req = self._request_options(fmt, options)
        if fmt == "png":
            views.populate_image(view, req)
            out.write(view.image)
        elif fmt == "pdf":
            views.populate_pdf(view, req)
            out.write(view.pdf)
        elif fmt == "csv":
            views.populate_csv(view, req)
            for chunk in view.csv:
                out.write(chunk)
        else:
            views.populate_excel(view, req)
            for chunk in view.excel:
                out.write(chunk)
