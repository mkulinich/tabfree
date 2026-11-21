"""Tableau-compatible permission checks (ADR-006), minimal Phase 1 version.

Evaluation for one capability on one dashboard, following the precedence ADR-006 records:

1. Site administrators are allowed everything; owners are allowed everything.
2. The site role is a ceiling: a capability above the role is denied whatever the rules say.
3. Rules come from the dashboard, or from its project when the project is locked (or the dashboard
   has no rules of its own, i.e. it inherits the project defaults).
4. A rule naming the user decides (allow or deny). Otherwise any group deny wins, then any group
   allow. Anything unspecified is denied.

Phase 3 (P3.3) moves rules to Postgres and checks this order against Tableau's own behaviour with an
effective-permission test matrix before the model is frozen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

Decision = Literal["allow", "deny"]


class Capability(StrEnum):
    VIEW = "view"
    FILTER = "filter"
    DOWNLOAD_SUMMARY = "download_summary_data"
    DOWNLOAD_FULL = "download_full_data"
    WEB_EDIT = "web_edit"
    OVERWRITE = "overwrite"
    DELETE = "delete"
    SET_PERMISSIONS = "set_permissions"
    #: Semantic models (Tableau's Connect on data sources): run ad-hoc semantic queries (explore).
    CONNECT = "connect"


class SiteRole(StrEnum):
    UNLICENSED = "unlicensed"
    VIEWER = "viewer"
    EXPLORER = "explorer"
    CREATOR = "creator"
    SITE_ADMINISTRATOR = "site_administrator"


#: The most each site role can ever do (Tableau's role "ceilings").
CEILINGS: dict[SiteRole, frozenset[Capability]] = {
    SiteRole.UNLICENSED: frozenset(),
    SiteRole.VIEWER: frozenset({Capability.VIEW, Capability.FILTER, Capability.DOWNLOAD_SUMMARY}),
    SiteRole.EXPLORER: frozenset(
        {
            Capability.VIEW,
            Capability.FILTER,
            Capability.DOWNLOAD_SUMMARY,
            Capability.DOWNLOAD_FULL,
            Capability.WEB_EDIT,
            Capability.CONNECT,
        }
    ),
    SiteRole.CREATOR: frozenset(Capability),
    SiteRole.SITE_ADMINISTRATOR: frozenset(Capability),
}


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
    locked: bool
    rules: tuple[Rule, ...]
    leaders: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DashboardEntry:
    id: str
    project: str
    owner: str | None
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class ModelEntry:
    """A semantic model's site entry: its permissions (Connect) and data version."""

    id: str
    owner: str | None
    rules: tuple[Rule, ...]
    data_version: str = ""


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


def decide(
    principal: Principal,
    capability: Capability,
    dashboard: DashboardEntry,
    project: Project,
) -> bool:
    rules = project.rules if project.locked or not dashboard.rules else dashboard.rules
    owners = {dashboard.owner} | set(project.leaders)
    return _evaluate(principal, capability, owners, rules)


def decide_model(principal: Principal, capability: Capability, model: ModelEntry) -> bool:
    """The same order for a semantic model, which has its own rules and no project (default deny)."""
    return _evaluate(principal, capability, {model.owner}, model.rules)


def _evaluate(
    principal: Principal, capability: Capability, owners: set[str | None], rules: tuple[Rule, ...]
) -> bool:
    if principal.site_role is SiteRole.SITE_ADMINISTRATOR:
        return True
    if capability not in CEILINGS[principal.site_role]:
        return False
    if principal.username in owners:
        return True
    for rule in rules:
        if rule.user == principal.username and capability in rule.capabilities:
            return rule.capabilities[capability] == "allow"
    group_decisions = {
        rule.capabilities[capability]
        for rule in rules
        if rule.group is not None and rule.group in principal.groups and capability in rule.capabilities
    }
    if "deny" in group_decisions:
        return False
    return "allow" in group_decisions
