"""Import a factory folder into a site (P5.4 governance, P5.6 items), and publish approved items.

Governance is merged, not replaced: users are added or updated (site role, email), group membership
follows Tableau, missing projects are created (parents first) and their default rules set, models are
published as revisions with their rules. Dashboards are **not** published on import: each becomes a
migration item whose bundle sits in a draft owned by the importing administrator, so the repair agent
and reviewers work on it before anyone else sees it. Approving an item publishes the draft with the
Tableau owner, project and rules, adds the old Tableau URLs as redirects and restores favorites.

Re-importing is safe: items already approved are left alone; others get the new bundle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, select

from tabfree_api.authoring import files as bundle_files
from tabfree_api.authoring.drafts import DraftStore
from tabfree_api.authz import SiteRole, parse_rules
from tabfree_api.content import ContentError, Site
from tabfree_api.db import schema as t
from tabfree_api.db.seed import create_site
from tabfree_api.db.store import DbSite
from tabfree_api.migration.fidelity import score
from tabfree_api.migration.store import MigrationError, MigrationStore

MAX_REFERENCE_BYTES = 8 * 1024 * 1024


@dataclass
class ImportResult:
    users: int = 0
    groups: int = 0
    projects_created: int = 0
    models: int = 0
    items: int = 0
    skipped_approved: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)  # delta: approved, changed in Tableau since
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _site(engine: Engine, site_id: str, create: bool, default_role: str) -> DbSite:
    with engine.connect() as conn:
        exists = conn.execute(select(t.sites.c.id).where(t.sites.c.id == site_id)).scalar()
    if exists is None:
        if not create:
            raise ContentError(f"no site {site_id!r} (pass create=True to create it)")
        return create_site(engine, site_id, site_id, default_site_role=default_role)
    return DbSite(engine, site_id)


def import_factory(
    engine: Engine,
    folder: Path,
    *,
    by: str,
    site_id: str | None = None,
    create: bool = False,
    delta: bool = False,
) -> ImportResult:
    """Load a factory folder. With ``delta`` (cutover, P7.7), approved dashboards whose Tableau workbook
    changed come back for review with the new conversion; the published revision stays until approved."""
    result = ImportResult()
    site = Site(folder / "site.yaml")
    raw = yaml.safe_load((folder / "site.yaml").read_text(encoding="utf-8"))
    extras = _json(folder / "governance.json")
    waves = _json(folder / "waves.json")
    sid = site_id or site.id
    db = _site(engine, sid, create, site.default_role.value)
    store = MigrationStore(engine, sid)
    if by not in db.users:
        db.ensure_user(by, by, SiteRole.SITE_ADMINISTRATOR)

    # -- users and groups ---------------------------------------------------------------------------
    members: dict[str, list[str]] = {}
    for user in site.users.values():
        db.ensure_user(user.username, user.fullname, user.site_role, email=user.email or None)
        result.users += 1
        for group in user.groups:
            members.setdefault(group, []).append(user.username)
    for setting in extras.get("groups", []) or []:
        role = setting.get("minimumSiteRole")
        db.ensure_group(
            str(setting["name"]),
            source=str(setting.get("source", "local")),
            minimum_site_role=SiteRole(role) if role else None,
        )
        result.groups += 1
    for group, usernames in sorted(members.items()):
        db.set_members(group, usernames)

    def known(rules: Any) -> Any:
        for rule in rules:
            if rule.user is not None and rule.user not in db.users:
                db.ensure_user(rule.user, rule.user)
        return rules

    # -- projects -----------------------------------------------------------------------------------
    existing = set(db.projects)
    pending = {p.id: p for p in site.projects.values()}
    while pending:
        ready = [p for p in pending.values() if p.parent is None or p.parent not in pending]
        if not ready:
            raise ContentError("project parents form a cycle")
        for project in ready:
            if project.owner and project.owner not in db.users:
                db.ensure_user(project.owner, project.owner)
            if project.id not in existing:
                db.create_project(
                    project.id,
                    project.name,
                    parent=project.parent,
                    owner=project.owner,
                    asset_permissions=project.asset_permissions,
                )
                result.projects_created += 1
            for content_type, rules in project.defaults.items():
                db.set_rules("project", project.id, known(rules), content_type=content_type)
            del pending[project.id]

    # -- models -------------------------------------------------------------------------------------
    content_meta = extras.get("content", {}) or {}
    for model_id, model in site.models.items():
        entry = site.model_entries.get(model_id)
        owner = entry.owner if entry else None
        if owner and owner not in db.users:
            db.ensure_user(owner, owner)
        meta = content_meta.get(f"model:{model_id}", {})
        db.publish(
            "model",
            model_id,
            model,
            name=str(model.get("title", model_id)),
            project=entry.project if entry else None,
            owner=owner,
            message="migrated from Tableau",
            by=by,
            origin={
                "system": "tableau",
                **{k: v for k, v in meta.items() if k in ("certified", "certificationNote")},
            },
        )
        db.set_rules("model", model_id, known(entry.rules if entry else ()))
        result.models += 1

    # -- dashboards → migration items ---------------------------------------------------------------
    drafts = DraftStore(engine, sid)
    entries: dict[str, dict[str, Any]] = {}
    for project in raw.get("projects", []) or []:
        for d in project.get("dashboards", []) or []:
            entries[str(d["id"])] = {"project": project["id"], **d}
    wave_items = (waves or {}).get("items", {}) or {}
    for folder_item in sorted((folder / "dashboards").iterdir()):
        report = _json(folder_item / "report.json")
        if not report:
            continue
        item_id = str(report["id"])
        current = store.get(item_id)
        reopened = False
        if current and current["status"] == "approved":
            if not delta:
                result.skipped_approved.append(item_id)
                continue
            reopened = True
        wave = wave_items.get(item_id, {})
        status = "retired" if wave.get("status") == "retire" else report.get("status", "manual")
        if reopened and status == "converted":
            status = "review"  # changed in Tableau after approval: a person looks again
        site_entry = entries.get(item_id, {})
        values: dict[str, Any] = {
            "title": str(report.get("title", item_id))[:255],
            "workbook": str(report.get("workbook", ""))[:255],
            "project_id": site_entry.get("project"),
            "owner": site_entry.get("owner"),
            "wave": wave.get("wave"),
            "usage": int(report.get("usage") or 0),
            "status": status,
            "report": {**report, "site": site_entry},
            "repair_iterations": 0,
        }
        source = folder_item / "source.json"
        if source.is_file() and source.stat().st_size <= 256 * 1024:
            values["report"]["source"] = _json(source)  # the Tableau IR excerpt, for the repair agent
        png = folder_item / "reference.png"
        if png.is_file() and png.stat().st_size <= MAX_REFERENCE_BYTES:
            values["reference_png"] = png.read_bytes()
        csvs = {}
        for query, ref in ((report.get("references") or {}).get("csv") or {}).items():
            path = folder_item / str(ref.get("file", ""))
            if path.is_file() and path.resolve().is_relative_to(folder_item.resolve()):
                csvs[str(query)] = path.read_text(encoding="utf-8", errors="replace")
        values["reference_csv"] = csvs
        if (folder_item / "tabfree.json").is_file():
            manifest = json.loads((folder_item / "tabfree.json").read_text(encoding="utf-8"))
            meta = content_meta.get(item_id, {})
            if meta.get("tags"):
                manifest["tags"] = [str(tag)[:50] for tag in meta["tags"]][:20]
            if meta.get("description") and not manifest.get("description"):
                manifest["description"] = str(meta["description"])[:2000]
            files = {
                bundle_files.MANIFEST: json.dumps(manifest, indent=2) + "\n",
                bundle_files.HTML: (folder_item / "index.html").read_text(encoding="utf-8"),
                bundle_files.NOTES: (folder_item / "README.md").read_text(encoding="utf-8"),
            }
            try:
                bundle_files.check_limits(files)
            except bundle_files.EditError as exc:
                values["status"] = "manual" if status != "retired" else status
                result.errors.append(f"{item_id}: {exc}")
            else:
                draft = drafts.get(current["draft_id"]) if current and current.get("draft_id") else None
                if draft is not None and draft.status == "open":
                    drafts.update_files(draft.id, files, agent_session=None)
                else:
                    draft = drafts.create(
                        by,
                        item_id,
                        files,
                        base_revision=None,
                        project=site_entry.get("project"),
                        max_open=None,
                    )
                values["draft_id"] = draft.id
        coverage = (report.get("coverage") or {}).get("fraction")
        values["fidelity"] = score(coverage=coverage, unsupported=report.get("unsupported") or [])
        store.upsert(item_id, values)
        store.event(
            item_id,
            "changed_in_tableau" if reopened else "import",
            by=by,
            note=f"status {values['status']}",
            data={"wave": values["wave"], "workbookUpdatedAt": report.get("workbookUpdatedAt")},
        )
        if reopened:
            result.reopened.append(item_id)
        result.items += 1

    # -- records for Phase 6 and the verification reports -------------------------------------------
    for i, job in enumerate(extras.get("jobs", []) or []):
        store.put_record("job", f"{job.get('kind')}:{i}", job, str(job.get("status", "pending")))
    for i, alert in enumerate(extras.get("alerts", []) or []):
        store.put_record("alert", str(i), alert, str(alert.get("status", "pending")))
    for i, view in enumerate(extras.get("customViews", []) or []):
        store.put_record("custom_view", str(i), view, str(view.get("status", "pending")))
    for user, favorites in (extras.get("favorites", {}) or {}).items():
        store.put_record("favorites", str(user), favorites)
    if extras.get("access"):
        store.put_record("access_reference", "tableau", extras["access"])
    for decision in (_json(folder / "decisions.json") or {}).get("queue", []) or []:
        store.put_record("decision", str(decision["sourceKey"]), decision)
    store.put_record("redirects", "site", dict(raw.get("redirects") or {}))
    store.put_record("governance_report", "factory", extras.get("report", {}))
    return result


def approve(engine: Engine, site_id: str, item_id: str, *, by: str, note: str = "") -> int:
    """Publish an item's draft as the Tableau owner, with its project rules, redirects and favorites."""
    store = MigrationStore(engine, site_id)
    item = store.get(item_id)
    if item is None:
        raise MigrationError(404, f"no migration item {item_id!r}")
    if not item.get("draft_id"):
        raise MigrationError(409, f"{item_id!r} has no converted bundle; rebuild it")
    drafts = DraftStore(engine, site_id)
    draft = drafts.get(str(item["draft_id"]))
    if draft is None or draft.status != "open":
        raise MigrationError(409, f"{item_id!r}: its draft is not open")
    db = DbSite(engine, site_id)
    entry: Mapping[str, Any] = (item.get("report") or {}).get("site") or {}
    owner = entry.get("owner")
    if owner and owner not in db.users:
        db.ensure_user(str(owner), str(owner))
    store.act(item_id, "approve", by=by, note=note)  # checks the status first
    document, other = bundle_files.to_revision(draft.files)
    document["id"] = item_id
    revision = db.publish(
        "dashboard",
        item_id,
        document,
        files=other,
        project=entry.get("project") or draft.project,
        owner=str(owner) if owner else by,
        message=note or "migrated from Tableau",
        by=by,
        origin=dict(document.get("source") or {"system": "tableau"}),
    )
    if "permissions" in entry:
        rules = parse_rules(entry.get("permissions") or [])
        for rule in rules:
            if rule.user is not None and rule.user not in db.users:
                db.ensure_user(rule.user, rule.user)
        db.set_rules("dashboard", item_id, rules)
    drafts.mark_published(draft.id, revision)
    found = store.records("redirects", ["site"])
    redirects: dict[str, Any] = dict(found[0]["data"] or {}) if found else {}
    for path, target in redirects.items():
        if target == item_id:
            db.set_legacy_url(str(path), "dashboard", item_id)
    for record in store.records("favorites"):
        wanted = any(f.get("kind") == "dashboard" and f.get("id") == item_id for f in record["data"] or [])
        if wanted and record["key"] in db.users:
            db.set_favorite(str(record["key"]), "dashboard", item_id, True)
    store.upsert(item_id, {"published_revision": revision})
    store.event(item_id, "publish", by=by, data={"revision": revision})
    return revision
