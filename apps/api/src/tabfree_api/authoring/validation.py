"""The validation pipeline (BUILD_PLAN P4.5), shared by "Edit with Claude", the MCP server and migration.

Every draft passes it before publishing; agents run it after each change and fix what it reports.

1. **manifest:** the schema, and every ``<tf-*>`` reference in ``index.html`` against ``tabfree.json``.
2. **compile:** every named query compiles on both dialects (Athena/Trino and Redshift).
3. **data:** every query runs as the author: errors fail, empty results and all-null measures warn.
4. **scripts:** custom JavaScript is scanned. Network, navigation and dynamic code fail (they are how
   a frame could move data out); storage warns. Any custom script needs a human review.
5. **accessibility:** images need alt text, charts need titles, buttons need names, headings in order.
6. **screenshot:** the draft against the published revision, when the export renderer is available.

Failures block publishing; warnings are shown to the person who publishes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Literal

from tabfree_api.authoring import files as bundle_files
from tabfree_schemas.bundle import RISKY_JS, check_bundle

Status = Literal["pass", "warn", "fail", "skip"]
DIALECTS = ("athena", "redshift")
MAX_DATA_QUERIES = 25
#: Above this share of changed pixels, the screenshot check asks for a close look.
SCREENSHOT_WARN = 0.5

#: Patterns that fail the scripts check (storage only warns: the sandbox has none anyway).
BLOCKING_JS = ("navigation", "network", "dynamic code")
_HANDLER = re.compile(r"^on[a-z]+$")


@dataclass
class Check:
    name: str
    status: Status = "pass"
    messages: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.status = "fail"
        self.messages.append(message)

    def warn(self, message: str) -> None:
        if self.status in ("pass", "skip"):
            self.status = "warn"
        self.messages.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "messages": self.messages}


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    @property
    def custom_js(self) -> bool:
        return any(c.name == "scripts" and c.messages for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.to_dict() for c in self.checks]}

    def summary(self) -> str:
        lines = [f"validation: {'ok' if self.ok else 'FAILED'}"]
        for c in self.checks:
            lines.append(f"- {c.name}: {c.status}")
            lines.extend(f"  - {m}" for m in c.messages[:20])
        return "\n".join(lines)


CompileFn = Callable[[Mapping[str, Any], str, str], Any]
RunFn = Callable[[Mapping[str, Any], str], Mapping[str, Any]]
ScreenshotFn = Callable[[], tuple[bytes, bytes] | None]


def validate(
    files: Mapping[str, str],
    *,
    compile_query: CompileFn | None = None,
    run_query: RunFn | None = None,
    screenshots: ScreenshotFn | None = None,
    model_allowed: Callable[[str], bool] | None = None,
    quick: bool = False,
) -> ValidationReport:
    """Run the pipeline. ``quick`` skips the data and screenshot checks (after every agent edit)."""
    report = ValidationReport()
    manifest_check = Check("manifest")
    report.checks.append(manifest_check)
    manifest: dict[str, Any] | None = None
    try:
        bundle_files.check_limits(files)
        manifest = bundle_files.parse_manifest(files)
    except bundle_files.EditError as exc:
        manifest_check.fail(str(exc))
    html = files.get(bundle_files.HTML, "")
    if manifest is not None:
        bundle_report = check_bundle(manifest, html)
        for error in bundle_report.errors:
            manifest_check.fail(error)
        for warning in bundle_report.warnings:
            if "custom JavaScript" not in warning:
                manifest_check.warn(warning)
        if model_allowed is not None:
            for alias, model in (manifest.get("models") or {}).items():
                if not model_allowed(str(model)):
                    manifest_check.fail(
                        f"tabfree.json: models.{alias}: you may not use model {model!r} (it needs View "
                        "and Connect, or it must be the model the published dashboard already uses)"
                    )

    report.checks.append(_scripts(html))
    report.checks.append(_accessibility(html))

    compile_check = Check("compile")
    report.checks.append(compile_check)
    sound = manifest is not None and manifest_check.status != "fail"
    if not sound or compile_query is None:
        compile_check.status = "skip"
        compile_check.messages.append("needs a valid manifest" if not sound else "no compiler available")
    else:
        for name in (manifest or {}).get("queries", {}):
            for dialect in DIALECTS:
                try:
                    compile_query(manifest or {}, name, dialect)
                except Exception as exc:
                    compile_check.fail(f"query {name!r} on {dialect}: {exc}")

    data_check = Check("data")
    report.checks.append(data_check)
    if quick or run_query is None or compile_check.status != "pass":
        data_check.status = "skip"
        data_check.messages.append(
            "runs before publishing" if quick or run_query is None else "needs every query to compile"
        )
    else:
        names = list((manifest or {}).get("queries", {}))
        for name in names[:MAX_DATA_QUERIES]:
            try:
                result = run_query(manifest or {}, name)
            except Exception as exc:
                data_check.fail(f"query {name!r} failed: {exc}")
                continue
            rows = result.get("rows") or []
            if not rows:
                data_check.warn(f"query {name!r} returns no rows for you with the default filters")
                continue
            if result.get("truncated"):
                data_check.warn(f"query {name!r} is truncated at {len(rows)} rows; add a limit or filters")
            for i, column in enumerate(result.get("columns", [])):
                if column.get("role") == "measure" and all(r[i] is None for r in rows):
                    data_check.warn(f"query {name!r}: {column['name']!r} is empty (null) in every row")
        if len(names) > MAX_DATA_QUERIES:
            data_check.warn(f"checked the first {MAX_DATA_QUERIES} of {len(names)} queries")

    shot = Check("screenshot")
    report.checks.append(shot)
    if quick or screenshots is None or not report.ok:
        shot.status = "skip"
        shot.messages.append(
            "needs the export renderer (TABFREE_EXPORT_RENDERER)"
            if screenshots is None
            else "runs before publishing, once the other checks pass"
        )
    else:
        from tabfree_api.authoring import png

        pair = screenshots()
        if pair is None:
            shot.status = "skip"
            shot.messages.append("no published revision to compare with")
        else:
            try:
                fraction = png.changed_fraction(*pair)
            except png.PngError as exc:
                shot.warn(f"could not compare the screenshots: {exc}")
            else:
                text = f"{fraction:.0%} of the dashboard's pixels changed"
                if fraction > SCREENSHOT_WARN:
                    shot.warn(text + "; look at the preview before publishing")
                else:
                    shot.messages.append(text)
    return report


# ---- HTML checks ---------------------------------------------------------------------------------


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.handlers: list[tuple[str, str, int]] = []
        self.links: list[tuple[str, str, int]] = []
        self.images: list[tuple[str, dict[str, str], int]] = []
        self.titled: list[tuple[str, dict[str, str], int]] = []
        self.headings: list[tuple[int, int]] = []
        self.buttons: list[tuple[dict[str, str], int]] = []
        self._script: list[str] | None = None
        self._button: tuple[dict[str, str], int, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        line = self.getpos()[0]
        for name, value in values.items():
            if _HANDLER.match(name):
                self.handlers.append((name, value, line))
        if tag == "script" and values.get("type", "") not in ("application/json", "application/ld+json"):
            self._script = []
        if tag in ("a", "form", "area") and (values.get("href") or values.get("action")):
            self.links.append((tag, values.get("href") or values.get("action") or "", line))
        if tag in ("img", "tf-image"):
            self.images.append((tag, values, line))
        if tag in ("tf-chart", "tf-crosstab", "tf-map"):
            self.titled.append((tag, values, line))
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self.headings.append((int(tag[1]), line))
        if tag == "button":
            self._button = (values, line, [])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None
        if tag == "button" and self._button is not None:
            values, line, text = self._button
            if not "".join(text).strip() and not values.get("aria-label"):
                self.buttons.append((values, line))
            self._button = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        if self._button is not None:
            self._button[2].append(data)


def _parse(html: str) -> _Scan:
    scan = _Scan()
    scan.feed(html)
    scan.close()
    return scan


def _scripts(html: str) -> Check:
    check = Check("scripts")
    scan = _parse(html)
    code = [s for s in scan.scripts if s.strip()] + [value for _n, value, _l in scan.handlers]
    for name, _value, line in scan.handlers:
        check.warn(f"index.html:{line}: inline {name} handler; prefer addEventListener in a script")
    for tag, target, line in scan.links:
        if not target.startswith("#"):
            check.fail(
                f"index.html:{line}: <{tag}> leads to {target[:80]!r}; dashboards cannot navigate away"
            )
    hits = sorted({label for text in code for label, pattern in RISKY_JS.items() if pattern.search(text)})
    for label in hits:
        if label in BLOCKING_JS:
            check.fail(f"custom JavaScript uses {label} APIs, which dashboards may not use")
        else:
            check.warn(f"custom JavaScript uses {label} APIs, which the sandbox does not provide")
    if code:
        check.warn("custom JavaScript needs a human review before publishing")
    return check


def _accessibility(html: str) -> Check:
    check = Check("accessibility")
    scan = _parse(html)
    for tag, attrs, line in scan.images:
        if "alt" not in attrs:
            check.warn(f'index.html:{line}: <{tag}> needs alt text (alt="" if it is decoration)')
    for tag, attrs, line in scan.titled:
        if not attrs.get("title") and not attrs.get("aria-label"):
            check.warn(f"index.html:{line}: <{tag}> needs a title (screen readers announce it)")
    for _attrs, line in scan.buttons:
        check.warn(f"index.html:{line}: <button> needs text or an aria-label")
    previous = 0
    for level, line in scan.headings:
        if previous and level > previous + 1:
            check.warn(f"index.html:{line}: heading level jumps from h{previous} to h{level}")
        previous = level
    return check
