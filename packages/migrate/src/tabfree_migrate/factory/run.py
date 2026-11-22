"""The factory run (``tabfree-migrate factory``): the harvest in, a factory folder out.

A run reads a ``tabfree/factory@0`` file::

    schema: tabfree/factory@0
    site: migrated                    # the TabFree site id the folder imports into
    downloads: raw/                   # the download sink (downloads/manifest.json), or a folder of
                                      # .twb/.twbx/.tds/.tdsx files
    inventory: inventory.db           # optional: the crawl (owners, projects, permissions, usage)
    repository: repository.db         # optional: the repository harvest (usage, alerts)
    groundTruth: raw/                 # optional: the capture sink (ground_truth/index.json)
    extracts: lake/                   # optional: a local copy of convert-batch output
    remap: remap.yaml                 # optional: connection remap rules (tabfree/remap@0)
    identity: { by: email }
    fallbackOwner: migration-owner
    waves: { sizes: [20, 100], retireBelow: 0 }

and writes the factory folder described in :mod:`tabfree_migrate.factory`. Paths are relative to the
file. Every step is deterministic; nothing is sent anywhere and nothing in a workbook is executed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tabfree_migrate.blobstore import LocalDirSink, Sink, load_json, sink_for
from tabfree_migrate.factory.baseline import notes_markdown, render
from tabfree_migrate.factory.governance import DashboardRef, Identity, ModelRef, plan_governance
from tabfree_migrate.factory.manifest import DashboardPlan, plan_workbook
from tabfree_migrate.factory.models import Estate, SourceDoc, generate_estate
from tabfree_migrate.factory.remap import ExtractCatalogue, Rules
from tabfree_migrate.factory.waves import Candidate, WaveSettings, plan_waves
from tabfree_migrate.twb import parse_file
from tabfree_schemas.bundle import check_bundle

SCHEMA = "tabfree/factory@0"
REPORT_SCHEMA = "tabfree/factory-report@0"
_WORKBOOKS = {".twb", ".twbx"}
_DATASOURCES = {".tds", ".tdsx"}


class FactoryError(ValueError):
    pass


@dataclass
class FactoryConfig:
    base: Path
    site: str = "migrated"
    downloads: str | None = None
    inventory: Path | None = None
    repository: Path | None = None
    ground_truth: str | None = None
    extracts: Path | None = None
    remap: Mapping[str, Any] = field(default_factory=dict)
    identity: Identity = field(default_factory=Identity)
    fallback_owner: str = "migration-owner"
    waves: WaveSettings = field(default_factory=WaveSettings)
    #: Delta migration (P7.7): only workbooks changed in Tableau after this ISO timestamp.
    since: str | None = None

    @classmethod
    def load(cls, path: Path) -> FactoryConfig:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if doc.get("schema") != SCHEMA:
            raise FactoryError(f"{path}: not a {SCHEMA} file")
        base = path.parent

        def local(key: str) -> Path | None:
            return (base / doc[key]) if doc.get(key) else None

        def location(key: str) -> str | None:
            value = doc.get(key)
            if not value:
                return None
            return str(value) if str(value).startswith("s3://") else str(base / str(value))

        remap: Mapping[str, Any] = {}
        if doc.get("remap"):
            remap = yaml.safe_load((base / doc["remap"]).read_text(encoding="utf-8")) or {}
        return cls(
            base=base,
            site=str(doc.get("site", "migrated")),
            downloads=location("downloads"),
            inventory=local("inventory"),
            repository=local("repository"),
            ground_truth=location("groundTruth"),
            extracts=local("extracts"),
            remap=remap,
            identity=Identity.from_document(doc.get("identity")),
            fallback_owner=str(doc.get("fallbackOwner", "migration-owner")),
            waves=WaveSettings.from_document(doc.get("waves")),
            since=str(doc["since"]) if doc.get("since") else None,
        )


# -- inputs ------------------------------------------------------------------------------------------


def load_inventory(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    from tabfree_migrate.inventory import InventoryStore

    store = InventoryStore(path)
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for item in store.objects():
            out.setdefault(item.type, []).append(
                {**item.data, "id": item.luid, "name": item.name, "updated_at": item.updated_at}
            )
        return out
    finally:
        store.close()


def load_repository(path: Path | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    if path is None or not path.exists():
        return {}, {}
    from tabfree_migrate.repository import RepositoryStore

    store = RepositoryStore(path)
    try:
        rows = {
            name: list(store.rows(name))
            for name in ("data_alerts", "data_alerts_recipients", "users", "views")
        }
        return rows, store.view_usage_by_luid()
    finally:
        store.close()


@dataclass
class Sources:
    workbooks: list[SourceDoc] = field(default_factory=list)
    datasources: list[SourceDoc] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)  # SourceDoc.key → local file
    #: Workbooks left out of a delta run (unchanged since ``since``).
    skipped: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def _doc(key: str, path: Path, sources: Sources, meta: Mapping[str, Any]) -> None:
    try:
        ir = parse_file(path)
    except Exception as exc:  # malformed workbooks are reported, never fatal
        sources.errors.append({"origin": key, "error": f"{type(exc).__name__}: {exc}"})
        return
    doc = SourceDoc(
        key,
        ir,
        name=str(meta.get("name") or path.stem),
        content_url=str(meta.get("content_url") or ""),
        project=meta.get("project_id"),
        owner=meta.get("owner_id"),
        luid=meta.get("luid"),
        updated_at=meta.get("updated_at"),
    )
    sources.files[key] = path
    (sources.workbooks if path.suffix.lower() in _WORKBOOKS else sources.datasources).append(doc)


def _after(value: Any, since: str | None) -> bool:
    """Changed after ``since`` (both ISO timestamps); unknown times count as changed."""
    if since is None or not value:
        return True
    from datetime import UTC, datetime

    def parse(text: str) -> datetime:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=UTC)

    return parse(str(value)) > parse(since)


def load_sources(
    location: str | None,
    inventory: Mapping[str, list[dict[str, Any]]],
    staging: Path,
    *,
    since: str | None = None,
) -> Sources:
    """Every downloaded file; with ``since`` (the delta migration, P7.7), only workbooks changed after it."""
    sources = Sources()
    if not location:
        return sources
    meta = {str(i["id"]): i for kind in ("workbooks", "datasources") for i in inventory.get(kind, [])}
    root = Path(location)
    if (
        not location.startswith("s3://")
        and root.is_dir()
        and not (root / "downloads" / "manifest.json").exists()
    ):
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() in _WORKBOOKS | _DATASOURCES:
                _doc(str(path.relative_to(root)), path, sources, {})
        return sources
    sink = sink_for(location)
    manifest = load_json(sink, "downloads/manifest.json")
    entries = sorted(dict(manifest.get("entries", {})).items())
    # Prefer the download without extracts (smaller); either parses the same.
    chosen: dict[str, Mapping[str, Any]] = {}
    for _id, entry in entries:
        if entry.get("error") or not entry.get("key"):
            continue
        luid = str(entry.get("luid"))
        if luid not in chosen or not entry.get("include_extract"):
            chosen[luid] = entry
    for luid, entry in sorted(chosen.items()):
        updated = (meta.get(luid) or {}).get("updated_at") or entry.get("updated_at")
        if entry.get("kind") == "workbook" and not _after(updated, since):
            sources.skipped.append(luid)
            continue
        key = str(entry["key"])
        suffix = Path(key).suffix
        if isinstance(sink, LocalDirSink):
            path = sink.root.joinpath(*key.split("/"))
        else:
            data = sink.get_bytes(key)
            if data is None:
                sources.errors.append({"origin": luid, "error": f"{key} is missing"})
                continue
            path = staging / f"{luid}{suffix}"
            path.write_bytes(data)
        info = dict(meta.get(luid, {}))
        info.setdefault("name", entry.get("name"))
        info["luid"] = luid
        info["updated_at"] = updated
        _doc(luid, path, sources, info)
    return sources


def load_extracts(path: Path | None) -> ExtractCatalogue | None:
    if path is None or not path.exists():
        return None
    reports = []
    for report in sorted(path.glob("extracts/*/report.json")):
        reports.append(json.loads(report.read_text(encoding="utf-8")))
    return ExtractCatalogue.from_reports(reports)


def ground_truth_index(location: str | None) -> tuple[Sink | None, list[dict[str, Any]]]:
    if not location:
        return None, []
    sink = sink_for(location)
    index = load_json(sink, "ground_truth/index.json")
    captures = [c for c in dict(index.get("captures", {})).values() if not c.get("error") and c.get("key")]
    return sink, captures


# -- the run -------------------------------------------------------------------------------------------


@dataclass
class FactoryResult:
    out: Path
    summary: dict[str, Any]


def _views_of(plan: DashboardPlan) -> tuple[str, ...]:
    """Tableau view names a plan stands for: the dashboard, or the worksheet published on its own."""
    return (plan.dashboard,) if plan.dashboard else tuple(s.worksheet for s in plan.sheets)


def _write_references(
    folder: Path,
    plan: DashboardPlan,
    workbook_luid: str | None,
    gt_sink: Sink | None,
    captures: list[dict[str, Any]],
) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    if gt_sink is None or not workbook_luid:
        return refs
    mine = [c for c in captures if str(c.get("workbook_luid")) == workbook_luid and not c.get("label")]
    views = set(_views_of(plan))
    png = next((c for c in mine if c.get("format") == "png" and c.get("view_name") in views), None)
    if png is not None:
        data = gt_sink.get_bytes(str(png["key"]))
        if data is not None:
            (folder / "reference.png").write_bytes(data)
            refs["png"] = {"file": "reference.png", "key": png["key"], "viewLuid": png.get("view_luid")}
    csvs: dict[str, Any] = {}
    for sheet in plan.sheets:
        capture = next(
            (c for c in mine if c.get("format") == "csv" and c.get("view_name") == sheet.worksheet), None
        )
        if capture is None or not sheet.spec:
            continue
        data = gt_sink.get_bytes(str(capture["key"]))
        if data is None:
            continue
        (folder / "reference").mkdir(exist_ok=True)
        (folder / "reference" / f"{sheet.query}.csv").write_bytes(data)
        csvs[sheet.query] = {"file": f"reference/{sheet.query}.csv", "key": capture["key"]}
    if csvs:
        refs["csv"] = csvs
    return refs


def _source_excerpt(ir: Mapping[str, Any], plan: DashboardPlan) -> dict[str, Any]:
    """The parts of the workbook IR the repair agent needs: this dashboard and its worksheets."""
    names = {s.worksheet for s in plan.sheets}
    return {
        "schema": "tabfree/factory-source@0",
        "dashboard": next((d for d in ir.get("dashboards", []) if d.get("name") == plan.dashboard), None),
        "worksheets": [
            {k: v for k, v in w.items() if k not in ("dependencies", "fieldInstances")}
            for w in ir.get("worksheets", [])
            if w.get("name") in names
        ],
        "parameters": ir.get("parameters", []),
    }


def _coverage(plan: DashboardPlan) -> dict[str, Any]:
    sheets = len(plan.sheets)
    converted = sum(1 for s in plan.sheets if s.spec and s.report.ok)
    partial = sum(1 for s in plan.sheets if s.spec and not s.report.ok)
    return {
        "sheets": sheets,
        "converted": converted,
        "partial": partial,
        "fraction": round((converted + 0.5 * partial) / sheets, 3) if sheets else 0.0,
    }


def run_factory(config: FactoryConfig, out: Path) -> FactoryResult:
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("dashboards", "models"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir()
    inventory = load_inventory(config.inventory)
    repository, usage_by_view = load_repository(config.repository)
    with tempfile.TemporaryDirectory(prefix="tabfree-factory-") as tmp:
        sources = load_sources(config.downloads, inventory, Path(tmp), since=config.since)
        rules = Rules.from_document(config.remap)
        estate: Estate = generate_estate(
            sources.datasources, sources.workbooks, rules, extracts=load_extracts(config.extracts)
        )
        gt_sink, captures = ground_truth_index(config.ground_truth)
        dashboard_refs: list[DashboardRef] = []
        reports: dict[str, dict[str, Any]] = {}
        unsupported: Counter[str] = Counter()
        candidates: list[Candidate] = []
        views = inventory.get("views", [])
        used_ids: set[str] = set()
        for source in sources.workbooks:
            try:
                plans = plan_workbook(source, estate)
            except Exception as exc:
                sources.errors.append({"origin": source.key, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for plan in plans:
                if plan.id in used_ids:  # two workbooks with the same names
                    plan.id = f"{plan.id[:91]}-{len(used_ids)}"
                    plan.manifest["id"] = plan.id
                used_ids.add(plan.id)
                folder = out / "dashboards" / plan.id
                folder.mkdir(parents=True)
                html = render(plan, package=sources.files.get(source.key))
                check = check_bundle(plan.manifest, html)
                broken = sorted({s.model.id for s in plan.sheets if s.model and s.model.report.schema_errors})
                for model_id in broken:
                    plan.unsupported.append(f"model {model_id!r} is not valid; fix it before this dashboard")
                converted = check.ok and not broken
                (folder / "source.json").write_text(
                    json.dumps(_source_excerpt(source.ir, plan), indent=1, default=str) + "\n",
                    encoding="utf-8",
                )
                if converted:
                    (folder / "tabfree.json").write_text(
                        json.dumps(plan.manifest, indent=2) + "\n", encoding="utf-8"
                    )
                    (folder / "index.html").write_text(html, encoding="utf-8")
                (folder / "README.md").write_text(notes_markdown(plan), encoding="utf-8")
                refs = _write_references(folder, plan, source.luid, gt_sink, captures)
                usage = sum(
                    usage_by_view.get(str(v["id"]), int(v.get("total_views") or 0))
                    for v in views
                    if str(v.get("workbook_id")) == str(source.luid) and v.get("name") in _views_of(plan)
                )
                report: dict[str, Any] = {
                    "schema": REPORT_SCHEMA,
                    "id": plan.id,
                    "title": plan.title,
                    "workbook": plan.workbook,
                    "workbookLuid": source.luid,
                    "workbookUpdatedAt": source.updated_at,
                    "origin": source.key,
                    "dashboard": plan.dashboard,
                    "views": list(_views_of(plan)),
                    "models": sorted({s.model.id for s in plan.sheets if s.model}),
                    "status": "converted" if converted else "manual",
                    "coverage": _coverage(plan),
                    "check": {"errors": check.errors, "warnings": check.warnings},
                    "unsupported": list(dict.fromkeys(plan.unsupported)),
                    "notes": list(dict.fromkeys(plan.notes)),
                    "references": refs,
                    "usage": usage,
                }
                (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                reports[plan.id] = report
                for item in report["unsupported"]:
                    unsupported[item.split(": ", 1)[-1].split(" (")[0][:80]] += 1
                if converted:
                    dashboard_refs.append(DashboardRef(plan.id, source.luid, plan.title, _views_of(plan)))
                candidates.append(Candidate(plan.id, plan.title, usage, converted, source.luid))
        # Models: only those a converted dashboard uses, plus every published data source.
        used_models = {m for r in reports.values() if r["status"] == "converted" for m in r["models"]}
        model_refs: list[ModelRef] = []
        for entry in estate.models.values():
            if entry.id not in used_models and not entry.published:
                continue
            path = out / "models" / f"{entry.id}.model.yaml"
            path.write_text(yaml.safe_dump(entry.doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
            luid = next((s.luid for s in sources.datasources if s.key == entry.origin), None)
            wb_luid = next((s.luid for s in sources.workbooks if s.key == entry.origin), None)
            model_refs.append(
                ModelRef(entry.id, luid if entry.published else None, None if entry.published else wb_luid)
            )
        (out / "models" / "report.json").write_text(
            json.dumps([e.summary() for e in estate.models.values()], indent=2) + "\n", encoding="utf-8"
        )
        (out / "decisions.json").write_text(
            json.dumps({"schema": "tabfree/decisions@0", "queue": estate.decision_queue()}, indent=2) + "\n",
            encoding="utf-8",
        )
        governance = plan_governance(
            inventory,
            dashboards=dashboard_refs,
            models=model_refs,
            site_id=config.site,
            identity=config.identity,
            fallback_owner=config.fallback_owner,
            repository=repository,
            usage={c.dashboard: c.usage for c in candidates},
        )
        (out / "site.yaml").write_text(
            "# Generated by tabfree-migrate factory: import with `python -m tabfree_api.migration import`.\n"
            + yaml.safe_dump(governance.site, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (out / "governance.json").write_text(
            json.dumps({**governance.extras, "report": governance.report}, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        waves = plan_waves(candidates, config.waves)
        (out / "waves.json").write_text(json.dumps(waves, indent=2) + "\n", encoding="utf-8")
    statuses = Counter(r["status"] for r in reports.values())
    wave_list: list[dict[str, Any]] = list(waves["waves"])
    summary = {
        "schema": "tabfree/factory-summary@0",
        "site": config.site,
        "workbooks": len(sources.workbooks),
        "datasources": len(sources.datasources),
        "dashboards": len(reports),
        "converted": statuses.get("converted", 0),
        "manual": statuses.get("manual", 0),
        "fullyConverted": sum(
            1 for r in reports.values() if r["status"] == "converted" and not r["unsupported"]
        ),
        "models": len(model_refs),
        "decisions": len(estate.decisions),
        "withReferencePng": sum(1 for r in reports.values() if "png" in r["references"]),
        "topUnsupported": unsupported.most_common(25),
        "errors": sources.errors + estate.errors,
        "governance": {k: len(v) for k, v in governance.report.items()},
        "waves": [{"wave": w["wave"], "dashboards": len(w["dashboards"])} for w in wave_list],
        "retire": len(waves["retire"]),
        "delta": {"since": config.since, "unchanged": len(sources.skipped)} if config.since else None,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return FactoryResult(out, summary)
