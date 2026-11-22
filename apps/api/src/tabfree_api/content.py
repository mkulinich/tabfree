"""The site: projects, dashboards, models and permissions, read from a site file.

Phase 1 keeps content on disk (a ``site.yaml`` that points at dashboard bundles and semantic
models). Phase 3 (P3.1) moves it to Postgres with immutable revisions; callers use this interface.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tabfree_api.authz import (
    Capability,
    ContentEntry,
    ModelEntry,
    Principal,
    Project,
    Rule,
    SiteRole,
    evaluate,
    parse_rules,
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")


class ContentError(ValueError):
    pass


@dataclass(frozen=True)
class SiteUser:
    username: str
    fullname: str
    site_role: SiteRole
    groups: frozenset[str]
    #: Admin-set attributes for RLS (``USERATTRIBUTE``); identity-provider claims override them.
    attributes: Mapping[str, str] = field(default_factory=dict)
    email: str = ""


@dataclass(frozen=True)
class SiteQueryConfig:
    """The site file's ``query`` section: per-site engine settings and cost guardrails.

    ``None`` means "use the service-wide default from the settings".
    """

    athena_workgroup: str | None = None
    daily_scan_budget_bytes: int | None = None
    max_concurrent_queries: int | None = None


@dataclass(frozen=True)
class Bundle:
    manifest: Mapping[str, Any]
    html: str


class SiteContent(Protocol):
    """What the portal and the query service read: the site file (Phase 1) or the database (P3.1)."""

    id: str
    models: Mapping[str, Mapping[str, Any]]

    @property
    def name(self) -> str: ...
    @property
    def default_role(self) -> SiteRole: ...
    @property
    def query(self) -> SiteQueryConfig: ...
    @property
    def projects(self) -> dict[str, Project]: ...
    @property
    def dashboards(self) -> dict[str, ContentEntry]: ...
    @property
    def model_entries(self) -> dict[str, ContentEntry]: ...
    @property
    def users(self) -> dict[str, SiteUser]: ...
    def site_role(self, username: str) -> SiteRole: ...
    def resolve(
        self, username: str, fullname: str, idp_groups: frozenset[str]
    ) -> tuple[str, frozenset[str], SiteRole]: ...
    def can(self, principal: Principal, capability: Capability, dashboard: str) -> bool: ...
    def can_model(self, principal: Principal, capability: Capability, model: str) -> bool: ...
    def data_version(self, model: str) -> str: ...
    def visible(self, principal: Principal) -> list[dict[str, str]]: ...
    def bundle(self, dashboard: str) -> Bundle: ...
    def resolve_legacy(self, path: str) -> tuple[str, str] | None: ...


class Site:
    def __init__(self, path: Path) -> None:
        from tabfree_schemas import load_document

        doc = load_document(path)
        if not isinstance(doc, Mapping):
            raise ContentError(f"{path}: not a site file")
        base = path.parent
        self.id = str(doc.get("id", "default"))
        if not _SLUG.match(self.id):
            raise ContentError(f"invalid site id {self.id!r}")
        self.dashboards_dir = (base / doc["dashboards"]).resolve()
        # ``models`` is the folder of *.model.yaml files, or {path, entries} with per-model settings.
        models_section = doc["models"]
        if isinstance(models_section, str):
            models_section = {"path": models_section}
        self.models_dir = (base / models_section["path"]).resolve()
        query = doc.get("query") or {}
        self.query = SiteQueryConfig(
            athena_workgroup=query.get("athenaWorkgroup"),
            daily_scan_budget_bytes=_optional_int(query.get("dailyScanBudgetBytes")),
            max_concurrent_queries=_optional_int(query.get("maxConcurrentQueries")),
        )
        self.default_role = SiteRole(doc.get("defaultSiteRole", "viewer"))
        self.projects: dict[str, Project] = {}
        self.dashboards: dict[str, ContentEntry] = {}
        for p in doc.get("projects", []):
            project = _project(p)
            if project.id in self.projects:
                raise ContentError(f"duplicate project id {project.id!r}")
            self.projects[project.id] = project
            for d in p.get("dashboards", []):
                entry = d if isinstance(d, Mapping) else {"id": d}
                if not _SLUG.match(entry["id"]):
                    raise ContentError(f"invalid dashboard id {entry['id']!r}")
                rules = parse_rules(entry.get("permissions", []))
                if not rules and "permissions" not in entry:
                    # Tableau copies the project's defaults onto content when it is published.
                    rules = project.defaults.get("dashboard", ())
                self.dashboards[entry["id"]] = ContentEntry(
                    entry["id"], project.id, entry.get("owner"), rules
                )
        for project in self.projects.values():
            if project.parent is not None and project.parent not in self.projects:
                raise ContentError(f"project {project.id!r} has an unknown parent {project.parent!r}")
        self.users: dict[str, SiteUser] = {
            u["username"]: SiteUser(
                u["username"],
                u.get("fullname", u["username"]),
                SiteRole(u.get("siteRole", self.default_role)),
                frozenset(u.get("groups", [])),
                {str(k): str(v) for k, v in (u.get("attributes") or {}).items()},
                str(u.get("email", "")),
            )
            for u in doc.get("users", [])
        }
        self.models: dict[str, Mapping[str, Any]] = {}
        for model_path in sorted(self.models_dir.glob("*.model.yaml")):
            model = load_document(model_path)
            self.models[str(model["id"])] = model
        #: Model permissions and data versions. A model without an entry can be explored only by site
        #: administrators (default deny).
        #: Old Tableau URLs (P3.9): "views/<workbook>/<sheet>" → dashboard id.
        self.redirects: dict[str, str] = {str(k): str(v) for k, v in (doc.get("redirects") or {}).items()}
        self.model_entries: dict[str, ContentEntry] = {}
        for m in models_section.get("entries", []):
            if m["id"] not in self.models:
                raise ContentError(f"the site file names an unknown model {m['id']!r}")
            self.model_entries[m["id"]] = ModelEntry(
                m["id"],
                m.get("owner"),
                parse_rules(m.get("permissions", [])),
                str(m.get("dataVersion", "")),
                m.get("project"),
            )

    def site_role(self, username: str) -> SiteRole:
        user = self.users.get(username)
        return user.site_role if user else self.default_role

    def resolve(
        self, username: str, fullname: str, idp_groups: frozenset[str]
    ) -> tuple[str, frozenset[str], SiteRole]:
        """Who signs in: identity-provider groups plus the site file's, and the site role."""
        user = self.users.get(username)
        if user is None:
            return fullname or username, idp_groups, self.default_role
        return fullname or user.fullname, idp_groups | user.groups, user.site_role

    @property
    def name(self) -> str:
        return self.id

    def resolve_legacy(self, path: str) -> tuple[str, str] | None:
        target = self.redirects.get(path)
        return ("dashboard", target) if target else None

    def can(self, principal: Principal, capability: Capability, dashboard: str) -> bool:
        entry = self.dashboards.get(dashboard)
        if entry is None:
            return False
        return evaluate(principal, capability, entry, self.projects).allowed

    def can_model(self, principal: Principal, capability: Capability, model: str) -> bool:
        if model not in self.models:
            return False
        entry = self.model_entries.get(model) or ModelEntry(model, None, ())
        return evaluate(principal, capability, entry, self.projects).allowed

    def data_version(self, model: str) -> str:
        """The model's data version from the site file ("" when unset); extract refresh bumps it."""
        entry = self.model_entries.get(model)
        return entry.data_version if entry else ""

    def visible(self, principal: Principal) -> list[dict[str, str]]:
        """Dashboards the principal may view, for browse (permission-filtered listing)."""
        out = []
        for entry in self.dashboards.values():
            if self.can(principal, Capability.VIEW, entry.id):
                bundle = self.bundle(entry.id)
                out.append(
                    {
                        "id": entry.id,
                        "title": str(bundle.manifest.get("title", entry.id)),
                        "project": self.projects[entry.project].name if entry.project else "",
                    }
                )
        return sorted(out, key=lambda d: (d["project"], d["title"]))

    def bundle(self, dashboard: str) -> Bundle:
        if dashboard not in self.dashboards:
            raise ContentError(f"no dashboard {dashboard!r}")
        folder = self.dashboards_dir / dashboard
        manifest = json.loads((folder / "tabfree.json").read_text(encoding="utf-8"))
        return Bundle(manifest, (folder / "index.html").read_text(encoding="utf-8"))


def _project(p: Mapping[str, Any]) -> Project:
    """A site-file project. ``permissions`` are the default rules for its dashboards (Phase 1);
    ``defaults`` gives them per content type ({project, dashboard, model, connection})."""
    if "assetPermissions" in p:
        mode = str(p["assetPermissions"])
    else:
        mode = "locked" if p.get("locked") else "customizable"
    if mode not in ("customizable", "locked", "locked_not_nested"):
        raise ContentError(f"project {p['id']!r}: unknown asset permissions {mode!r}")
    defaults: dict[str, tuple[Rule, ...]] = {
        kind: parse_rules(rules) for kind, rules in (p.get("defaults") or {}).items()
    }
    if "permissions" in p:
        defaults.setdefault("dashboard", parse_rules(p["permissions"]))
    leaders = [Rule(u, None, {Capability.PROJECT_LEADER: "allow"}) for u in p.get("leaders", [])]
    leaders += [Rule(None, g, {Capability.PROJECT_LEADER: "allow"}) for g in p.get("leaderGroups", [])]
    if leaders:
        defaults["project"] = (*defaults.get("project", ()), *leaders)
    return Project(
        str(p["id"]),
        str(p.get("name", p["id"])),
        mode,  # type: ignore[arg-type]
        defaults,
        p.get("owner"),
        p.get("parent"),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    number = int(value)
    if number < 0:
        raise ContentError(f"expected a non-negative number, got {value!r}")
    return number
