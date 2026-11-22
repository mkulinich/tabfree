"""Exports (BUILD_PLAN P3.7): summary data as CSV, a crosstab as Excel, and the view as PNG or PDF.

Every export checks the viewer's capability on the dashboard first (Download Summary Data for data,
Download Image/PDF for pictures), runs as the viewer (row-level security applies), and is audited
(format, query, rows; never values).

- **Data** goes through the governed query service with export caps, and table calcs computed in SQL
  where the compiler can push them down; calcs only the browser engine can compute are named in the
  ``X-TabFree-Omitted`` header.
- **Spreadsheet safety:** text starting with ``= + - @`` (or a tab or carriage return) is prefixed
  with ``'`` so spreadsheet programs never treat a data value as a formula.
- **Pictures** are rendered by headless Chromium signed in as the requesting user with a two-minute
  session, opening the same portal page in print mode with the same filters and parameters.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from tabfree_api.auth import SESSION_COOKIE, SessionInfo

ExportFormat = Literal["csv", "crosstab", "png", "pdf"]
_FORMULA = re.compile(r"^[=+\-@\t\r]")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
TOTAL = "__total"


def safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formulas in text (OWASP CSV injection)."""
    if isinstance(value, str) and _FORMULA.match(value):
        return "'" + value
    return value


def filename(*parts: str, extension: str) -> str:
    stem = "-".join(_SAFE_NAME.sub("_", p).strip("_") for p in parts if p) or "export"
    return f"{stem[:120]}.{extension}"


def _detail(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Sequence[Any]]]:
    """Columns and rows without total rows (Tableau's summary data has none)."""
    columns = list(payload["columns"])
    total = next((i for i, c in enumerate(columns) if c["name"] == TOTAL), None)
    rows = [r for r in payload["rows"] if total is None or r[total] is None]
    if total is not None:
        columns = [c for i, c in enumerate(columns) if i != total]
        rows = [[v for i, v in enumerate(r) if i != total] for r in rows]
    return columns, rows


def _csv_value(value: Any) -> Any:
    # 15 significant digits: the double's real precision, without binary artefacts (8604.529999999999).
    if isinstance(value, float):
        return format(value, ".15g")
    return safe_cell(value)


def to_csv(payload: Mapping[str, Any]) -> bytes:
    columns, rows = _detail(payload)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([safe_cell(c["name"]) for c in columns])
    for row in rows:
        writer.writerow(["" if v is None else _csv_value(v) for v in row])
    # A byte-order mark so Excel reads UTF-8.
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_xlsx(payload: Mapping[str, Any], title: str) -> bytes:
    """A crosstab sheet: bold headers, the model's number formats (Tableau's are Excel-compatible)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    columns, rows = _detail(payload)
    book = Workbook()
    sheet = book.active
    if sheet is None:  # a new workbook always has one sheet
        sheet = book.create_sheet()
    sheet.title = (_SAFE_NAME.sub(" ", title).strip() or "Data")[:31]
    sheet.append([str(c["name"]) for c in columns])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.data_type = "s"
    for row in rows:
        sheet.append([safe_cell(v) for v in row])
    for index, column in enumerate(columns, start=1):
        fmt = column.get("format")
        letter = sheet.cell(row=1, column=index).column_letter
        if fmt and not str(fmt).lower().startswith(("yyyy", "mm", "dd")):
            for cell in sheet[letter][1:]:
                cell.number_format = str(fmt)
        for cell in sheet[letter][1:]:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # never a formula, whatever the text
        sheet.column_dimensions[letter].width = max(10, min(50, len(str(column["name"])) + 4))
    sheet.freeze_panes = "A2"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class Renderer(Protocol):
    def render(
        self, info: SessionInfo, dashboard: str, state: Mapping[str, Any], fmt: Literal["png", "pdf"]
    ) -> bytes: ...


def print_url(base_url: str, dashboard: str, state: Mapping[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode().rstrip("=")
    return (
        f"{base_url.rstrip('/')}/dashboards/{urllib.parse.quote(dashboard, safe='')}?print=1&state={encoded}"
    )


class PlaywrightRenderer:
    """Headless Chromium as the requesting user (needs ``tabfree-api[render]`` and Chromium)."""

    def __init__(
        self, base_url: str, sessions: Any, *, timeout_s: float = 60.0, settle_ms: int = 750
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sessions = sessions
        self.timeout_s = timeout_s
        self.settle_ms = settle_ms

    def render(
        self, info: SessionInfo, dashboard: str, state: Mapping[str, Any], fmt: Literal["png", "pdf"]
    ) -> bytes:
        return self._render(info, print_url(self.base_url, dashboard, state), fmt)

    def render_path(
        self, info: SessionInfo, path: str, state: Mapping[str, Any], fmt: Literal["png", "pdf"]
    ) -> bytes:
        """A portal page in print mode, e.g. ``/drafts/<id>`` (validation and migration repair)."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("render_path takes a path on this server")
        if path.startswith("/dashboards/"):
            return self.render(info, urllib.parse.unquote(path.removeprefix("/dashboards/")), state, fmt)
        return self._render(info, f"{self.base_url}{path}?print=1", fmt)

    def _render(self, info: SessionInfo, url: str, fmt: Literal["png", "pdf"]) -> bytes:
        from playwright.sync_api import sync_playwright

        # A two-minute session with exactly the requester's identity: RLS and permissions apply.
        token = self.sessions.create(
            info.username,
            info.fullname,
            info.groups,
            info.site_role,
            120,
            email=info.email,
            attributes=info.attributes,
        )
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 900}, device_scale_factor=2
                    )
                    context.add_cookies(
                        [{"name": SESSION_COOKIE, "value": token, "url": self.base_url, "httpOnly": True}]
                    )
                    page = context.new_page()
                    page.set_default_timeout(self.timeout_s * 1000)
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_timeout(self.settle_ms)
                    if fmt == "pdf":
                        return bytes(page.pdf(print_background=True, landscape=True, format="A4"))
                    return bytes(page.screenshot(full_page=True, type="png"))
                finally:
                    browser.close()
        finally:
            self.sessions.delete(token)
