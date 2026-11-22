"""Tableau-compatible permissions (ADR-006, BUILD_PLAN P3.3).

The order follows Tableau's "effective permissions" documentation (checked 2026-11-20, see
ADR-006), for one capability on one resource:

1. **Site role.** A capability the site role does not permit is denied, whatever else applies
   (owners and project leaders included).
2. **Scenarios.** Site administrators have every capability. The owner or a leader of the project
   (or of any project above it: leadership applies downward) has every capability on the project's
   content. The content owner has every capability on their content, except Set Permissions when
   the project's asset permissions are locked.
3. **User rules.** A rule naming the user decides: denied, else allowed.
4. **Group rules.** Any group denied → denied; else any group allowed → allowed.
5. **Unspecified** → denied.

Which rules apply: content in a *customizable* project carries its own rules (copied from the
project's defaults when it is published). When the project is *locked* — or a project above it is
locked including nested projects — the controlling project's default rules for that content type
apply instead, and the content's own rules are ignored.

TabFree deviation, recorded in ADR-006: Tableau lets Viewers *Connect* to data sources (needed to
render views). In TabFree, Connect means ad-hoc semantic queries (the explore API), so it needs
Explorer; dashboards never need Connect to render.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

Decision = Literal["allow", "deny"]
ResourceKind = Literal["project", "dashboard", "model", "connection"]
AssetPermissions = Literal["customizable", "locked", "locked_not_nested"]


class Capability(StrEnum):
    # Projects
    PUBLISH = "publish"
    # Dashboards (Tableau workbooks and views)
    VIEW = "view"
    FILTER = "filter"
    VIEW_COMMENTS = "view_comments"
    ADD_COMMENTS = "add_comments"
    DOWNLOAD_IMAGE_PDF = "download_image_pdf"
    DOWNLOAD_SUMMARY = "download_summary_data"
    SHARE_CUSTOMIZED = "share_customized"
    DOWNLOAD_FULL = "download_full_data"
    #: Edit in the browser; in TabFree, "Edit with Claude".
    WEB_EDIT = "web_edit"
    #: Download the dashboard bundle (Tableau's Download Workbook / Save a Copy).
    DOWNLOAD_WORKBOOK = "download_workbook"
    OVERWRITE = "overwrite"
    MOVE = "move"
    DELETE = "delete"
    SET_PERMISSIONS = "set_permissions"
    # Semantic models (Tableau data sources) and connections
    CONNECT = "connect"
    DOWNLOAD_DATA_SOURCE = "download_data_source"
    REFRESH_EXTRACTS = "refresh_extracts"
    #: Not a capability in Tableau (a status set from a project rule's menu); stored as one.
    PROJECT_LEADER = "project_leader"


class SiteRole(StrEnum):
    UNLICENSED = "unlicensed"
    VIEWER = "viewer"
    EXPLORER = "explorer"
    EXPLORER_CAN_PUBLISH = "explorer_can_publish"
    CREATOR = "creator"
    SITE_ADMINISTRATOR = "site_administrator"


C = Capability
#: Capabilities that exist for each kind of resource.
APPLICABLE: dict[str, frozenset[Capability]] = {
    "project": frozenset({C.VIEW, C.PUBLISH}),
    "dashboard": frozenset(
        {
            C.VIEW,
            C.FILTER,
            C.VIEW_COMMENTS,
            C.ADD_COMMENTS,
            C.DOWNLOAD_IMAGE_PDF,
            C.DOWNLOAD_SUMMARY,
            C.SHARE_CUSTOMIZED,
            C.DOWNLOAD_FULL,
            C.WEB_EDIT,
            C.DOWNLOAD_WORKBOOK,
            C.OVERWRITE,
            C.MOVE,
            C.DELETE,
            C.SET_PERMISSIONS,
        }
    ),
    "model": frozenset(
        {
            C.VIEW,
            C.CONNECT,
            C.DOWNLOAD_DATA_SOURCE,
            C.OVERWRITE,
            C.MOVE,
            C.DELETE,
            C.SET_PERMISSIONS,
            C.REFRESH_EXTRACTS,
        }
    ),
    "connection": frozenset({C.VIEW, C.CONNECT, C.OVERWRITE, C.MOVE, C.DELETE, C.SET_PERMISSIONS}),
}
_VIEWER = {
    "project": {C.VIEW},
    "dashboard": {
        C.VIEW,
        C.FILTER,
        C.VIEW_COMMENTS,
        C.ADD_COMMENTS,
        C.DOWNLOAD_IMAGE_PDF,
        C.DOWNLOAD_SUMMARY,
    },
    "model": {C.VIEW},
    "connection": {C.VIEW},
}
_EXPLORER = {
    "project": {C.VIEW},
    "dashboard": _VIEWER["dashboard"]
    | {C.SHARE_CUSTOMIZED, C.DOWNLOAD_FULL, C.WEB_EDIT, C.DOWNLOAD_WORKBOOK},
    "model": {C.VIEW, C.CONNECT, C.DOWNLOAD_DATA_SOURCE},
    "connection": {C.VIEW, C.CONNECT},
}
_ALL = {kind: set(caps) for kind, caps in APPLICABLE.items()}
#: The most each site role can ever do, per resource kind (Tableau's site-role maximums).
CEILINGS: dict[SiteRole, dict[str, frozenset[Capability]]] = {
    role: {kind: frozenset(caps) for kind, caps in table.items()}
    for role, table in (
        (SiteRole.UNLICENSED, {kind: set() for kind in APPLICABLE}),
        (SiteRole.VIEWER, _VIEWER),
        (SiteRole.EXPLORER, _EXPLORER),
        (SiteRole.EXPLORER_CAN_PUBLISH, _ALL),
        (SiteRole.CREATOR, _ALL),
        (SiteRole.SITE_ADMINISTRATOR, _ALL),
    )
}
_RANK = {role: i for i, role in enumerate(SiteRole)}


def max_role(*roles: SiteRole) -> SiteRole:
    return max(roles, key=_RANK.__getitem__)


@dataclass(frozen=True)
class Principal:
    username: str
    groups: frozenset[str]
    site_role: SiteRole
    fullname: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """One grantee's decisions, e.g. group Managers: view allow, download_full_data deny."""

    user: str | None
    group: str | None
    capabilities: Mapping[Capability, Decision]


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    asset_permissions: AssetPermissions = "customizable"
    #: Default rules per content type ("project" for the project's own rules).
    defaults: Mapping[str, tuple[Rule, ...]] = field(default_factory=dict)
    owner: str | None = None
    parent: str | None = None

    @property
    def locked(self) -> bool:
        return self.asset_permissions != "customizable"

    def leads(self, principal: Principal) -> bool:
        return any(
            r.capabilities.get(C.PROJECT_LEADER) == "allow"
            and (r.user == principal.username or (r.group is not None and r.group in principal.groups))
            for r in self.defaults.get("project", ())
        )


@dataclass(frozen=True)
class ContentEntry:
    """A dashboard, model or connection: where it lives, who owns it and its own rules."""

    id: str
    project: str | None
    owner: str | None
    rules: tuple[Rule, ...]
    kind: ResourceKind = "dashboard"
    #: Models: bumped on extract refresh, part of every result-cache key.
    data_version: str = ""


#: Phase 1 names, kept for callers.
DashboardEntry = ContentEntry


def ModelEntry(  # noqa: N802 - Phase 1 constructor name
    id: str, owner: str | None, rules: tuple[Rule, ...], data_version: str = "", project: str | None = None
) -> ContentEntry:
    return ContentEntry(id, project, owner, rules, "model", data_version)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def parse_rules(raw: Iterable[Mapping[str, Any]]) -> tuple[Rule, ...]:
    rules = []
    for item in raw:
        user, group = item.get("user"), item.get("group")
        if (user is None) == (group is None):
            raise ValueError("each permission rule names exactly one user or group")
        caps = {Capability(k): v for k, v in (item.get("capabilities") or {}).items()}
        if any(v not in ("allow", "deny") for v in caps.values()):
            raise ValueError("permission decisions are 'allow' or 'deny'")
        rules.append(Rule(user, group, caps))
    return tuple(rules)


def ancestry(project: str | None, projects: Mapping[str, Project]) -> list[Project]:
    """The project and its parents, nearest first (cycles and unknown parents end the walk)."""
    chain: list[Project] = []
    seen: set[str] = set()
    while project is not None and project in projects and project not in seen:
        seen.add(project)
        chain.append(projects[project])
        project = projects[project].parent
    return chain


def controlling_project(chain: list[Project]) -> Project | None:
    """The project whose default rules lock this resource, if any (``chain`` nearest first)."""
    for ancestor in reversed(chain[1:]):  # from the top: a lock "including nested projects"
        if ancestor.asset_permissions == "locked":
            return ancestor
    if chain and chain[0].locked:
        return chain[0]
    return None


def evaluate(
    principal: Principal,
    capability: Capability,
    entry: ContentEntry | Project,
    projects: Mapping[str, Project],
) -> Verdict:
    """The effective permission, with the reason (for the audit log and the permissions dialog)."""
    controller: Project | None
    if isinstance(entry, Project):
        kind: str = "project"
        chain = ancestry(entry.id, projects) or [entry]
        own_rules = entry.defaults.get("project", ())
        content_owner = None
        # A project's own rules are locked only by a parent locked including nested projects.
        controller = next((p for p in reversed(chain[1:]) if p.asset_permissions == "locked"), None)
    else:
        kind = entry.kind
        chain = ancestry(entry.project, projects)
        own_rules = entry.rules
        content_owner = entry.owner
        controller = controlling_project(chain)
    if capability not in APPLICABLE[kind]:
        return Verdict(False, f"{capability} is not a capability of a {kind}")
    if capability not in CEILINGS[principal.site_role][kind]:
        return Verdict(False, f"site role {principal.site_role} does not permit {capability}")
    if principal.site_role is SiteRole.SITE_ADMINISTRATOR:
        return Verdict(True, "site administrator")
    for project in chain:
        if project.owner == principal.username:
            return Verdict(True, f"owner of project {project.name!r}")
        if project.leads(principal):
            return Verdict(True, f"leader of project {project.name!r}")
    locked_out = capability is C.SET_PERMISSIONS and controller is not None
    if content_owner is not None and content_owner == principal.username and not locked_out:
        return Verdict(True, "content owner")
    if controller is not None:
        rules = controller.defaults.get(kind, ())
        source = f"locked project {controller.name!r}"
    else:
        rules = own_rules
        source = "its own rules"
    for rule in rules:
        if rule.user == principal.username and capability in rule.capabilities:
            decision = rule.capabilities[capability]
            return Verdict(decision == "allow", f"user rule ({decision}) from {source}")
    groups = [
        (rule.group, rule.capabilities[capability])
        for rule in rules
        if rule.group is not None and rule.group in principal.groups and capability in rule.capabilities
    ]
    denied = sorted(g for g, d in groups if d == "deny")
    if denied:
        return Verdict(False, f"group {denied[0]!r} denied in {source}")
    allowed = sorted(g for g, d in groups if d == "allow")
    if allowed:
        return Verdict(True, f"group {allowed[0]!r} allowed in {source}")
    return Verdict(False, f"unspecified in {source}")


def decide(
    principal: Principal,
    capability: Capability,
    entry: ContentEntry,
    project: Project | Mapping[str, Project],
) -> bool:
    """``evaluate`` as a boolean; ``project`` is the content's project or every project by id."""
    projects = project if isinstance(project, Mapping) else {project.id: project}
    return evaluate(principal, capability, entry, projects).allowed


def decide_model(
    principal: Principal,
    capability: Capability,
    model: ContentEntry,
    projects: Mapping[str, Project] | None = None,
) -> bool:
    """A semantic model: its own rules, or its project's when locked (default deny)."""
    return evaluate(principal, capability, model, projects or {}).allowed


def capabilities(
    principal: Principal, entry: ContentEntry | Project, projects: Mapping[str, Project]
) -> dict[str, bool]:
    """Every applicable capability → allowed, for the UI (which buttons to show)."""
    kind = "project" if isinstance(entry, Project) else entry.kind
    return {
        str(cap): evaluate(principal, cap, entry, projects).allowed
        for cap in sorted(APPLICABLE[kind], key=list(Capability).index)
    }
