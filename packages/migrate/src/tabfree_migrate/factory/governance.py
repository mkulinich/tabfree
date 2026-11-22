"""Governance migration (BUILD_PLAN P5.4): who can see and do what, carried over from Tableau.

Input is the read-only harvest: the inventory crawl (users, groups, projects with their default
permissions, workbooks, data sources, views, schedules, tasks, subscriptions, and on request
favorites and custom views) and the repository harvest (data-driven alerts, usage). Output:

- a **site file** (``site.yaml``, the format the API imports): users mapped to identity-provider
  usernames, site roles, groups and members, the project tree with owners, locked or customizable
  asset permissions, default rules per content type and project leaders, every converted dashboard
  and model with its owner and rules, and old-URL redirects;
- **extras** (``governance.json``): favorites, custom views, subscriptions and extract-refresh tasks
  (as jobs with their schedules), data-driven alerts (decoded where the condition is readable, else
  marked for the owner to confirm), tags, descriptions and certifications;
- a **report**: grantees and owners that could not be mapped, orphaned content given the fallback
  owner, and Tableau capabilities TabFree has no equivalent for.

Users are mapped by ``identity`` settings: ``by: email`` (the default; the IdP's username is the
email) or ``by: username``, optionally lower-cased, with a domain appended to bare names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Tableau site role → TabFree site role.
SITE_ROLES = {
    "creator": "creator",
    "explorer": "explorer",
    "explorercanpublish": "explorer_can_publish",
    "siteadministrator": "site_administrator",
    "siteadministratorcreator": "site_administrator",
    "siteadministratorexplorer": "site_administrator",
    "serveradministrator": "site_administrator",
    "viewer": "viewer",
    "readonly": "viewer",
    "unlicensed": "unlicensed",
    "guest": "unlicensed",
}
#: Tableau capability → TabFree capability, per resource kind.
WORKBOOK_CAPABILITIES = {
    "Read": "view",
    "Filter": "filter",
    "ViewComments": "view_comments",
    "AddComment": "add_comments",
    "ExportImage": "download_image_pdf",
    "ExportData": "download_summary_data",
    "ShareView": "share_customized",
    "ViewUnderlyingData": "download_full_data",
    "WebAuthoring": "web_edit",
    "ExportXml": "download_workbook",
    "Write": "overwrite",
    "ChangeHierarchy": "move",
    "Delete": "delete",
    "ChangePermissions": "set_permissions",
}
DATASOURCE_CAPABILITIES = {
    "Read": "view",
    "Connect": "connect",
    "ExportXml": "download_data_source",
    "Write": "overwrite",
    "ChangeHierarchy": "move",
    "Delete": "delete",
    "ChangePermissions": "set_permissions",
    "ExtractRefresh": "refresh_extracts",
}
PROJECT_CAPABILITIES = {"Read": "view", "Write": "publish", "ProjectLeader": "project_leader"}
#: Capabilities TabFree deliberately has no equivalent for (reported, not an error).
IGNORED = {"CreateRefreshMetrics", "RunExplainData", "SaveAs", "InheritedProjectLeader", "Execute", "Pulse"}
ASSET_PERMISSIONS = {
    "lockedtoproject": "locked",
    "lockedtoprojectwithoutnested": "locked_not_nested",
    "managedbyowner": "customizable",
}
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str, fallback: str = "project") -> str:
    value = _SLUG.sub("-", text.lower()).strip("-") or fallback
    if not value[0].isalnum():
        value = f"p-{value}"
    return value[:100].rstrip("-")


@dataclass(frozen=True)
class Identity:
    by: str = "email"  # email | username
    lowercase: bool = True
    domain: str | None = None  # appended to bare usernames: "alice" → "alice@corp.example"

    @classmethod
    def from_document(cls, doc: Mapping[str, Any] | None) -> Identity:
        doc = doc or {}
        by = str(doc.get("by", "email"))
        if by not in ("email", "username"):
            raise ValueError("identity.by must be 'email' or 'username'")
        return cls(by, bool(doc.get("lowercase", True)), doc.get("domain"))

    def username(self, user: Mapping[str, Any]) -> str | None:
        name = str(user.get("name") or "")
        value = str(user.get("email") or "") if self.by == "email" else name
        if not value and self.by == "email":
            value = name  # no email on the account: fall back to the Tableau name
        if not value:
            return None
        if "\\" in value:  # DOMAIN\user
            value = value.split("\\", 1)[1]
        if self.domain and "@" not in value:
            value = f"{value}@{self.domain}"
        return value.lower() if self.lowercase else value


@dataclass(frozen=True)
class DashboardRef:
    """A converted dashboard and the Tableau content it came from."""

    id: str
    workbook_luid: str | None
    title: str
    view_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelRef:
    id: str
    datasource_luid: str | None
    #: For a model generated from a workbook's embedded data source.
    workbook_luid: str | None = None


@dataclass
class GovernancePlan:
    site: dict[str, Any]
    extras: dict[str, Any]
    report: dict[str, list[Any]] = field(default_factory=dict)

    def note(self, kind: str, entry: Any) -> None:
        self.report.setdefault(kind, []).append(entry)


def _key(text: Any) -> str:
    return re.sub(r"[^a-z]", "", str(text or "").lower())


def _items(inventory: Mapping[str, Iterable[Mapping[str, Any]]], kind: str) -> list[Mapping[str, Any]]:
    return list(inventory.get(kind, []) or [])


class _Mapper:
    def __init__(
        self,
        inventory: Mapping[str, Iterable[Mapping[str, Any]]],
        identity: Identity,
        fallback_owner: str,
        plan: GovernancePlan,
    ) -> None:
        self.plan = plan
        self.fallback_owner = fallback_owner
        self.users: dict[str, str] = {}  # luid → username
        self.user_rows: dict[str, Mapping[str, Any]] = {}
        self.inactive: set[str] = set()
        for user in _items(inventory, "users"):
            luid = str(user.get("id") or user.get("luid"))
            name = identity.username(user)
            if name is None:
                plan.note("unmappedUsers", {"luid": luid, "reason": "no name or email"})
                continue
            if name in self.users.values():
                plan.note(
                    "unmappedUsers", {"luid": luid, "username": name, "reason": "two Tableau users map to it"}
                )
                continue
            self.users[luid] = name
            self.user_rows[luid] = user
            if _key(user.get("site_role")) in ("unlicensed", "guest"):
                self.inactive.add(luid)
        self.groups: dict[str, str] = {}  # luid → group name
        self.group_rows: dict[str, Mapping[str, Any]] = {}
        for group in _items(inventory, "groups"):
            luid = str(group.get("id") or group.get("luid"))
            self.groups[luid] = str(group.get("name"))
            self.group_rows[luid] = group

    def owner(self, luid: Any, what: str) -> str:
        name = self.users.get(str(luid)) if luid else None
        if name is None or str(luid) in self.inactive:
            self.plan.note("orphaned", {"content": what, "owner": luid, "fallback": self.fallback_owner})
            return self.fallback_owner
        return name

    def rules(
        self, raw: Iterable[Mapping[str, Any]], table: Mapping[str, str], where: str
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rule in raw or []:
            grantee = _key(rule.get("grantee_type"))
            gid = str(rule.get("grantee_id"))
            entry: dict[str, Any] = {}
            if grantee == "user":
                name = self.users.get(gid)
                if name is None:
                    self.plan.note("unmappedGrantees", {"where": where, "user": gid})
                    continue
                entry["user"] = name
            elif grantee == "group":
                name = self.groups.get(gid)
                if name is None:
                    self.plan.note("unmappedGrantees", {"where": where, "group": gid})
                    continue
                entry["group"] = name
            else:
                self.plan.note("unmappedGrantees", {"where": where, "grantee": rule.get("grantee_type")})
                continue
            caps: dict[str, str] = {}
            for cap, mode in (rule.get("capabilities") or {}).items():
                if cap in table:
                    caps[table[cap]] = "deny" if _key(mode) == "deny" else "allow"
                elif cap not in IGNORED:
                    self.plan.note("unknownCapabilities", {"where": where, "capability": cap})
            if caps:
                entry["capabilities"] = caps
                out.append(entry)
        return out


def _leaders(rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Split project-leader grants out of project rules (the site file lists them separately)."""
    users: list[str] = []
    groups: list[str] = []
    rest: list[dict[str, Any]] = []
    for rule in rules:
        caps = dict(rule["capabilities"])
        if caps.pop("project_leader", None) == "allow":
            (users if "user" in rule else groups).append(str(rule.get("user") or rule.get("group")))
        if caps:
            rest.append({**rule, "capabilities": caps})
    return rest, users, groups


def _schedule(schedules: Mapping[str, Mapping[str, Any]], sid: Any, inline: Any) -> dict[str, Any]:
    raw = dict(inline or {}) if isinstance(inline, Mapping) else {}
    if sid and str(sid) in schedules:
        raw = {**dict(schedules[str(sid)]), **raw}
    out: dict[str, Any] = {}
    for key in ("name", "frequency", "frequency_details", "schedule_type", "next_run_at", "timezone"):
        if raw.get(key) not in (None, "", {}):
            out[key] = raw[key]
    return out


_OPERATORS = {
    "above": ">",
    "greater": ">",
    "greater-than": ">",
    "above-or-equal": ">=",
    "greater-or-equal": ">=",
    "below": "<",
    "less": "<",
    "less-than": "<",
    "below-or-equal": "<=",
    "less-or-equal": "<=",
    "equal": "=",
    "equal-to": "=",
}


def decode_alert(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The condition of a data-driven alert, when the repository row says it plainly."""
    operator = row.get("condition_operator") or row.get("operator") or row.get("condition")
    threshold = row.get("threshold") if row.get("threshold") is not None else row.get("condition_value")
    if operator is None or threshold is None:
        return None
    op = _OPERATORS.get(str(operator).strip().lower().replace("_", "-"))
    if op is None:
        return None
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        return None
    return {"operator": op, "threshold": value}


def plan_governance(
    inventory: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    dashboards: Sequence[DashboardRef],
    models: Sequence[ModelRef],
    site_id: str = "migrated",
    identity: Identity | None = None,
    fallback_owner: str = "admin",
    repository: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    usage: Mapping[str, int] | None = None,
) -> GovernancePlan:
    """The site file and extras for one Tableau site."""
    plan = GovernancePlan({}, {})
    ident = identity or Identity()
    m = _Mapper(inventory, ident, fallback_owner, plan)
    repository = repository or {}

    # -- users and groups ---------------------------------------------------------------------------
    members: dict[str, list[str]] = {}
    group_settings: list[dict[str, Any]] = []
    for gluid, group in m.group_rows.items():
        name = m.groups[gluid]
        for uid in group.get("member_ids", []) or []:
            if str(uid) in m.users:
                members.setdefault(str(uid), []).append(name)
        source = "local" if _key(group.get("domain_name")) in ("", "local") else "idp"
        setting: dict[str, Any] = {"name": name, "source": source}
        role = SITE_ROLES.get(_key(group.get("minimum_site_role")))
        if role:
            setting["minimumSiteRole"] = role  # Tableau's "grant role on sign in"
        group_settings.append(setting)
    users = [{"username": fallback_owner, "fullname": "Migration owner", "siteRole": "site_administrator"}]
    for luid, name in sorted(m.users.items(), key=lambda kv: kv[1]):
        row = m.user_rows[luid]
        role = SITE_ROLES.get(_key(row.get("site_role")))
        if role is None:
            plan.note(
                "unmappedUsers",
                {"luid": luid, "username": name, "reason": f"site role {row.get('site_role')!r}"},
            )
            role = "viewer"
        entry: dict[str, Any] = {
            "username": name,
            "fullname": str(row.get("fullname") or row.get("name") or name),
            "siteRole": role,
        }
        if row.get("email"):
            entry["email"] = str(row["email"])
        if members.get(luid):
            entry["groups"] = sorted(set(members[luid]))
        if name == fallback_owner:
            users[0] = entry | {"siteRole": "site_administrator"}
        else:
            users.append(entry)

    # -- projects -----------------------------------------------------------------------------------
    project_rows = _items(inventory, "projects")
    project_ids: dict[str, str] = {}
    used: set[str] = set()
    by_luid = {str(p.get("id") or p.get("luid")): p for p in project_rows}

    def path(p: Mapping[str, Any]) -> str:
        parts, seen = [str(p.get("name"))], set()
        parent = p.get("parent_id")
        while parent and str(parent) in by_luid and parent not in seen:
            seen.add(parent)
            parts.insert(0, str(by_luid[str(parent)].get("name")))
            parent = by_luid[str(parent)].get("parent_id")
        return "-".join(parts)

    for luid, p in by_luid.items():
        base = slug(path(p))
        pid, n = base, 2
        while pid in used:
            pid = f"{base[:95]}-{n}"
            n += 1
        used.add(pid)
        project_ids[luid] = pid
    projects: dict[str, dict[str, Any]] = {}
    for luid, p in by_luid.items():
        pid = project_ids[luid]
        where = f"project {p.get('name')!r}"
        rules, leader_users, leader_groups = _leaders(
            m.rules(p.get("permissions") or [], PROJECT_CAPABILITIES, where)
        )
        entry = {
            "id": pid,
            "name": str(p.get("name")),
            "assetPermissions": ASSET_PERMISSIONS.get(_key(p.get("content_permissions")), "customizable"),
            "owner": m.owner(p.get("owner_id"), where),
            "defaults": {
                "project": rules,
                "dashboard": m.rules(
                    p.get("default_workbook_permissions") or [], WORKBOOK_CAPABILITIES, where
                ),
                "model": m.rules(
                    p.get("default_datasource_permissions") or [], DATASOURCE_CAPABILITIES, where
                ),
            },
            "dashboards": [],
        }
        if p.get("description"):
            entry["description"] = str(p["description"])
        if p.get("parent_id") and str(p["parent_id"]) in project_ids:
            entry["parent"] = project_ids[str(p["parent_id"])]
        if leader_users:
            entry["leaders"] = sorted(set(leader_users))
        if leader_groups:
            entry["leaderGroups"] = sorted(set(leader_groups))
        projects[pid] = entry
    if not projects:
        projects["migrated"] = {"id": "migrated", "name": "Migrated from Tableau", "dashboards": []}

    # -- dashboards ---------------------------------------------------------------------------------
    workbooks = {str(w.get("id") or w.get("luid")): w for w in _items(inventory, "workbooks")}
    views = {str(v.get("id") or v.get("luid")): v for v in _items(inventory, "views")}
    datasources = {str(d.get("id") or d.get("luid")): d for d in _items(inventory, "datasources")}
    redirects: dict[str, str] = {}
    content_meta: dict[str, dict[str, Any]] = {}
    view_to_dashboard: dict[str, str] = {}
    workbook_to_dashboards: dict[str, list[str]] = {}
    for ref in dashboards:
        wb = workbooks.get(str(ref.workbook_luid)) if ref.workbook_luid else None
        where = f"dashboard {ref.id!r}"
        dpid = project_ids.get(str(wb.get("project_id"))) if wb else None
        dpid = dpid or next(iter(projects))
        dentry: dict[str, Any] = {"id": ref.id, "owner": m.owner(wb.get("owner_id") if wb else None, where)}
        if wb is not None and wb.get("permissions") is not None:
            dentry["permissions"] = m.rules(wb.get("permissions") or [], WORKBOOK_CAPABILITIES, where)
        projects[dpid]["dashboards"].append(dentry)
        meta: dict[str, Any] = {}
        if wb is not None:
            workbook_to_dashboards.setdefault(str(ref.workbook_luid), []).append(ref.id)
            if wb.get("tags"):
                meta["tags"] = sorted(str(t) for t in wb["tags"])
            if wb.get("description"):
                meta["description"] = str(wb["description"])
            for vluid, view in views.items():
                if str(view.get("workbook_id")) != str(ref.workbook_luid):
                    continue
                if str(view.get("name")) in ref.view_names:
                    view_to_dashboard[vluid] = ref.id
                    url = str(view.get("content_url") or "")
                    parts = url.split("/sheets/")
                    if len(parts) == 2:
                        redirects[f"views/{parts[0]}/{parts[1]}"] = ref.id
        if meta:
            content_meta[ref.id] = meta

    # -- models -------------------------------------------------------------------------------------
    model_entries: list[dict[str, Any]] = []
    datasource_to_model: dict[str, str] = {}
    for mref in models:
        ds = datasources.get(str(mref.datasource_luid)) if mref.datasource_luid else None
        wb = workbooks.get(str(mref.workbook_luid)) if mref.workbook_luid else None
        where = f"model {mref.id!r}"
        msource: Mapping[str, Any] | None = ds or wb
        mentry: dict[str, Any] = {
            "id": mref.id,
            "owner": m.owner(msource.get("owner_id") if msource else None, where),
        }
        mpid = project_ids.get(str(msource.get("project_id"))) if msource else None
        if mpid:
            mentry["project"] = mpid
        if ds is not None:
            datasource_to_model[str(mref.datasource_luid)] = mref.id
            if ds.get("permissions") is not None:
                mentry["permissions"] = m.rules(ds["permissions"], DATASOURCE_CAPABILITIES, where)
            elif mpid:
                # Not crawled: Tableau copies the project's defaults onto content when it is published.
                mentry["permissions"] = projects[mpid]["defaults"]["model"]
            mmeta: dict[str, Any] = {}
            if ds.get("certified"):
                mmeta["certified"] = True
                if ds.get("certification_note"):
                    mmeta["certificationNote"] = str(ds["certification_note"])
            if ds.get("tags"):
                mmeta["tags"] = sorted(str(t) for t in ds["tags"])
            if ds.get("description"):
                mmeta["description"] = str(ds["description"])
            if mmeta:
                content_meta[f"model:{mref.id}"] = mmeta
        elif wb is not None:
            # Embedded data sources follow their workbook's rules: viewers of the dashboard may view.
            mentry["permissions"] = [
                r | {"capabilities": {k: v for k, v in r["capabilities"].items() if k == "view"}}
                for r in m.rules(wb.get("permissions") or [], WORKBOOK_CAPABILITIES, where)
                if "view" in r["capabilities"]
            ]
        model_entries.append(mentry)

    # -- extras: favorites, custom views, subscriptions, refreshes, alerts ---------------------------
    favorites: dict[str, list[dict[str, str]]] = {}
    for fav in _items(inventory, "favorites"):
        user = m.users.get(str(fav.get("user_id")))
        if user is None:
            continue
        out = []
        for vid in fav.get("views", []) or []:
            if str(vid) in view_to_dashboard:
                out.append({"kind": "dashboard", "id": view_to_dashboard[str(vid)]})
        for wid in fav.get("workbooks", []) or []:
            out += [{"kind": "dashboard", "id": d} for d in workbook_to_dashboards.get(str(wid), [])[:1]]
        for did in fav.get("datasources", []) or []:
            if str(did) in datasource_to_model:
                out.append({"kind": "model", "id": datasource_to_model[str(did)]})
        if out:
            favorites[user] = [dict(t) for t in {tuple(sorted(o.items())) for o in out}]
    custom_views = []
    for cv in _items(inventory, "custom_views"):
        view = cv.get("view") or {}
        owner = cv.get("owner") or {}
        dashboard = view_to_dashboard.get(str(view.get("id") if isinstance(view, Mapping) else view))
        custom_views.append(
            {
                "name": str(cv.get("name")),
                "owner": m.users.get(str(owner.get("id") if isinstance(owner, Mapping) else owner)),
                "dashboard": dashboard,
                "shared": bool(cv.get("shared")),
                "status": "recreate" if dashboard else "view-not-migrated",
            }
        )
    schedules = {str(s.get("id") or s.get("luid")): s for s in _items(inventory, "schedules")}
    jobs: list[dict[str, Any]] = []
    for sub in _items(inventory, "subscriptions"):
        target = sub.get("target") or {}
        tid = str(target.get("id") if isinstance(target, Mapping) else target)
        dashboard = view_to_dashboard.get(tid) or next(iter(workbook_to_dashboards.get(tid, [])), None)
        job = {
            "kind": "subscription",
            "subject": str(sub.get("subject") or ""),
            "user": m.users.get(str(sub.get("user_id"))),
            "dashboard": dashboard,
            "schedule": _schedule(schedules, sub.get("schedule_id"), sub.get("schedule")),
            "attachImage": bool(sub.get("attach_image", True)),
            "attachPdf": bool(sub.get("attach_pdf", False)),
            "onlyIfData": not bool(sub.get("send_if_view_empty", True)),
            "status": "pending" if dashboard else "content-not-migrated",
        }
        jobs.append(job)
    for task in _items(inventory, "tasks"):
        target = task.get("target") or {}
        tid = str(target.get("id") if isinstance(target, Mapping) else target)
        model = datasource_to_model.get(tid)
        jobs.append(
            {
                "kind": "refresh",
                "model": model,
                "workbook": tid if model is None else None,
                "type": str(task.get("task_type") or "extractRefresh"),
                "incremental": "increment"
                in str(task.get("refresh_type") or task.get("task_type") or "").lower(),
                "schedule": _schedule(schedules, task.get("schedule_id"), task.get("schedule_item")),
                "status": "pending" if model else "embedded-extract",
            }
        )
    repo_users = {str(u.get("id")): str(u.get("luid")) for u in repository.get("users", []) or []}
    repo_views = {str(v.get("id")): str(v.get("luid")) for v in repository.get("views", []) or []}
    recipients: dict[str, list[str]] = {}
    for r in repository.get("data_alerts_recipients", []) or []:
        uid = repo_users.get(str(r.get("recipient_id") or r.get("user_id")))
        if uid and uid in m.users:
            recipients.setdefault(str(r.get("data_alert_id")), []).append(m.users[uid])
    alerts = []
    for alert in repository.get("data_alerts", []) or []:
        view_luid = repo_views.get(str(alert.get("view_id")))
        owner_luid = repo_users.get(str(alert.get("creator_id") or alert.get("owner_id")))
        condition = decode_alert(alert)
        dashboard = view_to_dashboard.get(str(view_luid))
        alerts.append(
            {
                "title": str(alert.get("title") or ""),
                "owner": m.users.get(str(owner_luid)),
                "dashboard": dashboard,
                "condition": condition,
                "recipients": sorted(set(recipients.get(str(alert.get("id")), []))),
                "frequency": alert.get("frequency"),
                "status": "pending" if condition and dashboard else "needs-owner-confirmation",
            }
        )

    # Access parity reference (P5.5): what each user could open in Tableau ("Query Workbooks for User").
    access: dict[str, list[str]] = {}
    for uw in _items(inventory, "user_workbooks"):
        user = m.users.get(str(uw.get("user_id")))
        if user is None:
            continue
        access[user] = sorted(
            {d for wid in uw.get("workbook_ids", []) or [] for d in workbook_to_dashboards.get(str(wid), [])}
        )

    plan.site = {
        "id": site_id,
        "dashboards": "dashboards",
        "models": {"path": "models", "entries": model_entries},
        "defaultSiteRole": "viewer",
        "projects": list(projects.values()),
        "users": users,
        "redirects": dict(sorted(redirects.items())),
    }
    plan.extras = {
        "schema": "tabfree/governance@0",
        "groups": group_settings,
        "favorites": favorites,
        "customViews": custom_views,
        "jobs": jobs,
        "alerts": alerts,
        "content": content_meta,
        "usage": dict(usage or {}),
        "access": access,
    }
    return plan
