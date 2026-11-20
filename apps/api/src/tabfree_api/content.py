"""The site: projects, dashboards, models and permissions, read from a site file.

Phase 1 keeps content on disk (a ``site.yaml`` that points at dashboard bundles and semantic
models). Phase 3 (P3.1) moves it to Postgres with immutable revisions; callers use this interface.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tabfree_api.authz import (
    Capability,
    DashboardEntry,
    Principal,
    Project,
    SiteRole,
    decide,
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


@dataclass(frozen=True)
class Bundle:
    manifest: Mapping[str, Any]
    html: str


class Site:
    def __init__(self, path: Path) -> None:
        from tabfree_schemas import load_document

        doc = load_document(path)
        if not isinstance(doc, Mapping):
            raise ContentError(f"{path}: not a site file")
        base = path.parent
        self.dashboards_dir = (base / doc["dashboards"]).resolve()
        self.models_dir = (base / doc["models"]).resolve()
        self.default_role = SiteRole(doc.get("defaultSiteRole", "viewer"))
        self.projects: dict[str, Project] = {}
        self.dashboards: dict[str, DashboardEntry] = {}
        for p in doc.get("projects", []):
            project = Project(
                p["id"],
                p.get("name", p["id"]),
                bool(p.get("locked", False)),
                parse_rules(p.get("permissions", [])),
                frozenset(p.get("leaders", [])),
            )
            self.projects[project.id] = project
            for d in p.get("dashboards", []):
                entry = d if isinstance(d, Mapping) else {"id": d}
                if not _SLUG.match(entry["id"]):
                    raise ContentError(f"invalid dashboard id {entry['id']!r}")
                self.dashboards[entry["id"]] = DashboardEntry(
                    entry["id"], project.id, entry.get("owner"), parse_rules(entry.get("permissions", []))
                )
        self.users: dict[str, SiteUser] = {
            u["username"]: SiteUser(
                u["username"],
                u.get("fullname", u["username"]),
                SiteRole(u.get("siteRole", self.default_role)),
                frozenset(u.get("groups", [])),
            )
            for u in doc.get("users", [])
        }
        self.models: dict[str, Mapping[str, Any]] = {}
        for model_path in sorted(self.models_dir.glob("*.model.yaml")):
            model = load_document(model_path)
            self.models[str(model["id"])] = model

    def site_role(self, username: str) -> SiteRole:
        user = self.users.get(username)
        return user.site_role if user else self.default_role

    def can(self, principal: Principal, capability: Capability, dashboard: str) -> bool:
        entry = self.dashboards.get(dashboard)
        if entry is None:
            return False
        return decide(principal, capability, entry, self.projects[entry.project])

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
                        "project": self.projects[entry.project].name,
                    }
                )
        return sorted(out, key=lambda d: (d["project"], d["title"]))

    def bundle(self, dashboard: str) -> Bundle:
        if dashboard not in self.dashboards:
            raise ContentError(f"no dashboard {dashboard!r}")
        folder = self.dashboards_dir / dashboard
        manifest = json.loads((folder / "tabfree.json").read_text(encoding="utf-8"))
        return Bundle(manifest, (folder / "index.html").read_text(encoding="utf-8"))
