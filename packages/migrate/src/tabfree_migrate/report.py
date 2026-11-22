"""Inventory report (BUILD_PLAN P1.6): what we are migrating, how hard it is, and what to do with it.

Inputs, any of which may be missing:

- parsed workbooks (the construct census and per-workbook complexity),
- the crawler's inventory database (owners, projects, sizes, extract vs live),
- usage per workbook (views in the last 180 days, from the repository harvest), as a CSV with
  ``workbook,views_180d`` columns.

Output: ``report.md`` for people and ``report.json`` for tools. Triage is a proposal: "retire" always
needs the owner's confirmation, and the thresholds are first guesses to calibrate on real migrations.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from tabfree_migrate.census import Census, census_of

Triage = Literal["migrate", "rebuild", "retire"]

#: Connection classes whose data lives in files or extracts: these become Iceberg tables on S3.
FILE_CLASSES = frozenset({"hyper", "dataengine", "textscan", "excel-direct", "excel", "csv", "ogrdirect"})
#: Connection classes that are AWS services.
AWS_CLASSES = frozenset(
    {"athena", "awsathena", "redshift", "aurora", "aurorapostgres", "auroramysql", "s3", "amazon-s3"}
)
REBUILD_SCORE = 150.0
RETIRE_AFTER_DAYS = 180


@dataclass
class WorkbookReport:
    name: str
    path: str
    census: Census
    project: str | None = None
    owner: str | None = None
    size_bytes: int | None = None
    views_180d: int | None = None
    unsupported_functions: Counter[str] = field(default_factory=Counter)
    unparseable_calcs: int = 0
    triage: Triage = "migrate"
    reasons: list[str] = field(default_factory=list)
    #: TabFree work this workbook waits for (compiler gaps), as opposed to reasons to rebuild it.
    blockers: list[str] = field(default_factory=list)


@dataclass
class Connection:
    workbook: str
    datasource: str
    cls: str
    server: str | None
    location: Literal["aws", "file", "outside_aws"]
    extract: bool


def connection_location(cls: str, server: str | None) -> Literal["aws", "file", "outside_aws"]:
    if cls in FILE_CLASSES:
        return "file"
    if cls in AWS_CLASSES or (server or "").lower().endswith(".amazonaws.com"):
        return "aws"
    return "outside_aws"


def connections_of(name: str, ir: Mapping[str, Any]) -> list[Connection]:
    out = []
    for ds in ir.get("datasources", []):
        if ds.get("kind") == "parameters":
            continue
        extract = bool((ds.get("extract") or {}).get("enabled"))
        named = [n.get("connection", n) for n in ds.get("namedConnections", [])]
        conns = named or ([ds["connection"]] if ds.get("connection") else [])
        for conn in conns:
            cls = str(conn.get("class", ""))
            if not cls or cls == "federated":
                continue
            server = conn.get("server")
            out.append(
                Connection(
                    name,
                    str(ds.get("caption") or ds.get("name")),
                    cls,
                    server,
                    connection_location(cls, server),
                    extract,
                )
            )
    return out


def _supported_functions() -> frozenset[str] | None:
    try:
        from tabfree_compiler.calc import AGGREGATES, TABLE_CALCS
        from tabfree_compiler.sql import _FUNCTIONS
    except ImportError:  # the compiler is optional for the migration toolkit
        return None
    return frozenset(_FUNCTIONS) | AGGREGATES | TABLE_CALCS


def _compiler_readiness(ir: Mapping[str, Any], supported: frozenset[str] | None) -> tuple[Counter[str], int]:
    """Functions the compiler does not translate yet, and formulas it cannot parse."""
    unsupported: Counter[str] = Counter()
    unparseable = 0
    if supported is None:
        return unsupported, unparseable
    from tabfree_compiler.calc import CalcSyntaxError, Call, parse, walk

    for ds in ir.get("datasources", []):
        for col in ds.get("columns", []):
            formula = (col.get("calculation") or {}).get("formula")
            if not formula:
                continue
            try:
                tree = parse(formula)
            except CalcSyntaxError:
                unparseable += 1
                continue
            unsupported.update(n.name for n in walk(tree) if isinstance(n, Call) and n.name not in supported)
    return unsupported, unparseable


def blockers(wb: WorkbookReport) -> list[str]:
    out = []
    if wb.unsupported_functions:
        out.append("compiler support for " + ", ".join(f for f, _ in wb.unsupported_functions.most_common(5)))
    if wb.unparseable_calcs:
        out.append(f"{wb.unparseable_calcs} calculations that do not parse")
    return out


def triage(wb: WorkbookReport) -> tuple[Triage, list[str]]:
    reasons = []
    if wb.views_180d == 0:
        return "retire", [f"no views in {RETIRE_AFTER_DAYS} days (confirm with the owner)"]
    t = wb.census.totals
    for key, why in (
        ("hard_functions", "RAWSQL, SCRIPT or spatial functions"),
        ("extensions", "dashboard or viz extensions"),
        ("blended_worksheets", "data blending"),
    ):
        if t.get(key):
            reasons.append(f"{why} ({t[key]})")
    if wb.census.score() >= REBUILD_SCORE:
        reasons.append(f"complexity score {wb.census.score()} ≥ {REBUILD_SCORE}")
    return ("rebuild" if reasons else "migrate"), reasons


def load_usage(path: Path) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["workbook"]: int(row["views_180d"]) for row in csv.DictReader(handle)}


def repository_usage(
    db: Path, now: dt.datetime | None = None, days: int = RETIRE_AFTER_DAYS
) -> dict[str, int]:
    """Views per workbook name in the last ``days`` days, from the repository harvest (P1.3).

    ``views_stats`` keeps one row per user and view, with a running count (``nviews``) and the time of
    the last view. A workbook counts as used when any of its views was last seen inside the window;
    its number is the running total of those rows (an upper bound on views in the window). Workbooks
    whose views were all last seen earlier get 0.
    """
    from tabfree_migrate.repository import RepositoryStore

    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=days)
    store = RepositoryStore(db)
    try:
        workbook_names = {
            int(r["id"]): str(r["name"]) for r in store.rows("workbooks") if r.get("id") is not None
        }
        view_workbook = {
            int(r["id"]): int(r["workbook_id"])
            for r in store.rows("views")
            if r.get("workbook_id") is not None
        }
        usage = dict.fromkeys(workbook_names.values(), 0)
        for row in store.rows("views_stats"):
            workbook = workbook_names.get(view_workbook.get(int(row.get("view_id") or -1), -1))
            seen = _timestamp(row.get("time"))
            if workbook is not None and seen is not None and seen >= cutoff:
                usage[workbook] += int(row.get("nviews") or 0)
        return usage
    finally:
        store.close()


def _timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def inventory_details(db: Path) -> dict[str, dict[str, Any]]:
    """Owner, project and size per workbook name from the crawler's inventory database."""
    from tabfree_migrate.inventory import InventoryStore

    store = InventoryStore(db)
    try:
        out = {}
        for item in store.objects("workbooks"):
            data = item.data
            out[item.name] = {
                "project": data.get("project_name") or data.get("project"),
                "owner": data.get("owner_name") or data.get("owner_id"),
                "size_bytes": data.get("size"),
            }
        return out
    finally:
        store.close()


def build(
    irs: Iterable[tuple[str, Mapping[str, Any]]],
    usage: Mapping[str, int] | None = None,
    inventory: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[WorkbookReport], list[Connection], Census]:
    supported = _supported_functions()
    workbooks: list[WorkbookReport] = []
    connections: list[Connection] = []
    estate = Census()
    for path, ir in irs:
        name = Path(path).stem
        census = census_of(dict(ir))
        estate = estate + census
        unsupported, unparseable = _compiler_readiness(ir, supported)
        details = (inventory or {}).get(name, {})
        wb = WorkbookReport(
            name=name,
            path=path,
            census=census,
            project=details.get("project"),
            owner=details.get("owner"),
            size_bytes=details.get("size_bytes"),
            views_180d=(usage or {}).get(name),
            unsupported_functions=unsupported,
            unparseable_calcs=unparseable,
        )
        wb.triage, wb.reasons = triage(wb)
        wb.blockers = blockers(wb)
        workbooks.append(wb)
        connections += connections_of(name, ir)
    workbooks.sort(key=lambda w: -w.census.score())
    return workbooks, connections, estate


def to_json(workbooks: list[WorkbookReport], connections: list[Connection], estate: Census) -> dict[str, Any]:
    return {
        "estate": estate.to_dict(),
        "triage": dict(Counter(w.triage for w in workbooks)),
        "workbooks": [
            {
                "name": w.name,
                "path": w.path,
                "project": w.project,
                "owner": w.owner,
                "sizeBytes": w.size_bytes,
                "views180d": w.views_180d,
                "score": w.census.score(),
                "triage": w.triage,
                "reasons": w.reasons,
                "blockers": w.blockers,
                "totals": dict(w.census.totals),
                "unsupportedFunctions": dict(w.unsupported_functions),
            }
            for w in workbooks
        ],
        "connections": [c.__dict__ for c in connections],
    }


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    def cell(v: Any) -> str:
        return str(v if v is not None else "—").replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def to_markdown(workbooks: list[WorkbookReport], connections: list[Connection], estate: Census) -> str:
    t = estate.totals
    triage_counts = Counter(w.triage for w in workbooks)
    calcs = t.get("calculated_fields", 0)
    unsupported = sum((w.unsupported_functions for w in workbooks), Counter[str]())
    affected = sum(1 for w in workbooks if w.unsupported_functions)
    location = Counter(c.location for c in connections)
    rls = sorted(
        (w for w in workbooks if w.census.totals.get("user_functions")),
        key=lambda w: (-w.census.totals["user_functions"], w.name),
    )
    parts = [
        "# Tableau estate inventory",
        "",
        "Generated by `tabfree-migrate report` (BUILD_PLAN P1.6). Triage is a proposal; retiring "
        "content always needs the owner's confirmation.",
        "",
        "## Summary",
        "",
        _table(
            [
                "Workbooks",
                "Worksheets",
                "Dashboards",
                "Calculated fields",
                "LOD expressions",
                "Table calcs",
                "Data sources",
            ],
            [
                [
                    len(workbooks),
                    t.get("worksheets", 0),
                    t.get("dashboards", 0),
                    calcs,
                    t.get("lod_expressions", 0),
                    t.get("table_calculations", 0) + t.get("quick_table_calcs", 0),
                    t.get("datasources", 0),
                ]
            ],
        ),
        "",
        f"**Triage:** {triage_counts.get('migrate', 0)} migrate, {triage_counts.get('rebuild', 0)} rebuild, "
        f"{triage_counts.get('retire', 0)} retire.",
        "",
        "## Connections",
        "",
        f"{len(connections)} connections: {location.get('file', 0)} file or extract (become Iceberg tables), "
        f"{location.get('aws', 0)} already in AWS, {location.get('outside_aws', 0)} outside AWS "
        "(need a network path or a copy).",
        "",
        _table(
            ["Class", "Connections", "Outside AWS", "Extracts"],
            (
                [
                    cls,
                    n,
                    sum(1 for c in connections if c.cls == cls and c.location == "outside_aws"),
                    sum(1 for c in connections if c.cls == cls and c.extract),
                ]
                for cls, n in Counter(c.cls for c in connections).most_common()
            ),
        ),
        "",
        "## Row-level security",
        "",
        f"{len(rls)} workbooks use user functions (USERNAME, ISMEMBEROF…), "
        f"{t.get('user_functions', 0)} calls in all. "
        "These need RLS policies in the semantic model and persona tests (P3.4).",
        "",
        _table(
            ["Workbook", "User-function calls"], ([w.name, w.census.totals["user_functions"]] for w in rls)
        ),
        "",
        "## Compiler readiness",
        "",
        f"{affected} of {len(workbooks)} workbooks use functions the compiler does not translate yet. "
        "This is TabFree's backlog, not a reason to rebuild the workbook.",
        "",
        _table(["Function", "Uses"], unsupported.most_common(20)),
        "",
        "## Construct census",
        "",
        "### Functions (top 30)",
        "",
        _table(["Function", "Uses"], estate.functions.most_common(30)),
        "",
        "### Marks, filters and actions",
        "",
        _table(["Mark", "Panes"], estate.marks.most_common()),
        "",
        _table(["Filter", "Count"], estate.filters.most_common()),
        "",
        _table(["Action", "Count"], estate.actions.most_common()),
        "",
        "## Workbooks (most complex first)",
        "",
        _table(
            ["Workbook", "Project", "Score", "Views (180 d)", "Triage", "Why", "Waits for"],
            (
                [
                    w.name,
                    w.project,
                    w.census.score(),
                    w.views_180d,
                    w.triage,
                    "; ".join(w.reasons),
                    "; ".join(w.blockers),
                ]
                for w in workbooks
            ),
        ),
        "",
    ]
    return "\n".join(parts)


def write_report(
    out: Path, workbooks: list[WorkbookReport], connections: list[Connection], estate: Census
) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    md = out / "report.md"
    js = out / "report.json"
    md.write_text(to_markdown(workbooks, connections, estate), encoding="utf-8")
    js.write_text(
        json.dumps(to_json(workbooks, connections, estate), indent=2, default=str), encoding="utf-8"
    )
    return md, js
