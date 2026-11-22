"""Portal APIs (P3.5, P3.8): browse and search, favorites and recents, the model browser, revision
history, the permissions dialog, and site administration (users, groups, settings, audit log).

No ``from __future__ import annotations`` here: FastAPI must evaluate the ``Depends`` annotations.

Every listing is permission-filtered: an item a viewer may not View is absent, never "forbidden".
Write operations need the metadata database (``TABFREE_DATABASE_URL``); in the Phase 1 site-file mode
they answer 501.
"""

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import (
    APPLICABLE,
    Capability,
    ContentEntry,
    Principal,
    Project,
    Rule,
    SiteRole,
    ancestry,
    capabilities,
    controlling_project,
    evaluate,
    parse_rules,
)
from tabfree_api.content import ContentError


def tableau_order(caps: Any) -> list[Capability]:
    """Capabilities in Tableau's column order (the enum's order), not alphabetically."""
    order = list(Capability)
    return sorted(caps, key=order.index)


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
Kind = Literal["dashboard", "model"]


class RuleBody(BaseModel):
    user: str | None = Field(default=None, max_length=255)
    group: str | None = Field(default=None, max_length=255)
    capabilities: dict[str, Literal["allow", "deny"]]


class PermissionsBody(BaseModel):
    rules: list[RuleBody] = Field(max_length=500)
    #: Projects only: which default rules to replace (project, dashboard, model, connection).
    contentType: str | None = None  # noqa: N815 - JSON field name


class ProjectBody(BaseModel):
    id: str = Field(max_length=100)
    name: str = Field(min_length=1, max_length=255)
    parent: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=4000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    assetPermissions: Literal["customizable", "locked", "locked_not_nested"] | None = None  # noqa: N815
    owner: str | None = Field(default=None, max_length=255)


class UserBody(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    fullname: str = Field(default="", max_length=255)
    email: str | None = Field(default=None, max_length=320)
    siteRole: SiteRole = SiteRole.VIEWER  # noqa: N815


class UserPatch(BaseModel):
    siteRole: SiteRole | None = None  # noqa: N815
    disabled: bool | None = None
    fullname: str | None = Field(default=None, max_length=255)
    attributes: dict[str, str] | None = None


class GroupBody(BaseModel):
    members: list[str] = Field(default_factory=list, max_length=10_000)
    minimumSiteRole: SiteRole | None = None  # noqa: N815


class LegacyUrlBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    kind: Kind = "dashboard"
    id: str = Field(min_length=1, max_length=100)


class SitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    defaultSiteRole: SiteRole | None = None  # noqa: N815
    settings: dict[str, Any] | None = None


_SITE_SETTINGS = {
    "athenaWorkgroup": str,
    "dailyScanBudgetBytes": int,
    "maxConcurrentQueries": int,
    # Phase 4: "none" or "required" (a project leader approves drafts before they are published).
    "authoringApproval": str,
    "aiDailyTokensPerUser": int,
    "aiDailyTokensPerSite": int,
}
_CHOICES = {"authoringApproval": ("none", "required")}


def add_routes(
    router: APIRouter, ctx: Any, current: Callable[..., SessionInfo], csrf: Callable[..., None]
) -> None:
    site = ctx.site
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806

    def database() -> Any:
        if not hasattr(site, "publish"):
            raise HTTPException(501, "this needs the metadata database (TABFREE_DATABASE_URL)")
        return site

    def entry(kind: str, item: str) -> ContentEntry:
        found = (site.dashboards if kind == "dashboard" else site.model_entries).get(item)
        if found is None:
            raise HTTPException(404, f"no {kind} {item!r}")
        return found  # type: ignore[no-any-return]

    def visible_entry(info: SessionInfo, kind: str, item: str) -> ContentEntry:
        found = entry(kind, item)
        if not evaluate(info.principal(), Capability.VIEW, found, site.projects).allowed:
            raise HTTPException(404, f"no {kind} {item!r}")  # 404, not 403: no probing
        return found

    def is_admin(info: SessionInfo) -> bool:
        return info.site_role is SiteRole.SITE_ADMINISTRATOR

    def require_admin(info: SessionInfo) -> None:
        if not is_admin(info):
            raise HTTPException(403, "site administrators only")

    def manages(principal: Principal, project_id: str | None) -> bool:
        """Administrators, and owners or leaders of the project or a project above it."""
        if principal.site_role is SiteRole.SITE_ADMINISTRATOR:
            return True
        if principal.site_role not in (SiteRole.EXPLORER_CAN_PUBLISH, SiteRole.CREATOR):
            return False
        return any(
            p.owner == principal.username or p.leads(principal) for p in ancestry(project_id, site.projects)
        )

    def project_path(project_id: str | None) -> list[str]:
        return [p.name for p in reversed(ancestry(project_id, site.projects))]

    def info_for(kind: str) -> dict[str, dict[str, Any]]:
        return site.content_info(kind) if hasattr(site, "content_info") else {}

    def items(info: SessionInfo, kind: str) -> list[dict[str, Any]]:
        principal = info.principal()
        entries = site.dashboards if kind == "dashboard" else site.model_entries
        details = info_for(kind)
        favorites = set(site.favorites(info.username)) if hasattr(site, "favorites") else set()
        out = []
        for item in entries.values():
            if not evaluate(principal, Capability.VIEW, item, site.projects).allowed:
                continue
            extra = details.get(item.id, {})
            name = extra.get("name")
            if name is None:
                name = (
                    str(site.bundle(item.id).manifest.get("title", item.id))
                    if kind == "dashboard"
                    else str(site.models.get(item.id, {}).get("title", item.id))
                )
            origin = extra.get("origin") or {}
            out.append(
                {
                    "kind": kind,
                    "id": item.id,
                    "name": name,
                    "description": extra.get("description", ""),
                    "project": item.project,
                    "projectPath": project_path(item.project),
                    "owner": item.owner,
                    "updatedAt": extra.get("updatedAt"),
                    "favorite": (kind, item.id) in favorites,
                    "workbook": origin.get("workbook") if isinstance(origin, dict) else None,
                }
            )
        return sorted(out, key=lambda d: (d["projectPath"], str(d["name"]).casefold()))

    # ---- browse --------------------------------------------------------------------------------------

    @router.get("/api/site")
    def site_info(info: Me) -> dict[str, Any]:
        return {
            "id": site.id,
            "name": site.name,
            "database": hasattr(site, "publish"),
            "admin": is_admin(info),
            "canCreateProjects": is_admin(info),
        }

    @router.get("/api/projects")
    def projects(info: Me) -> list[dict[str, Any]]:
        """Projects the viewer may View (with their content counts) and what they can do in each."""
        principal = info.principal()
        dashboards = items(info, "dashboard")
        models = items(info, "model")
        out = []
        for project in site.projects.values():
            visible = evaluate(principal, Capability.VIEW, project, site.projects).allowed
            count = sum(1 for d in dashboards + models if d["project"] == project.id)
            if not (visible or count or manages(principal, project.id)):
                continue
            out.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "parent": project.parent,
                    "path": project_path(project.id),
                    "assetPermissions": project.asset_permissions,
                    "owner": project.owner,
                    "items": count,
                    "canPublish": evaluate(principal, Capability.PUBLISH, project, site.projects).allowed,
                    "canManage": manages(principal, project.id),
                }
            )
        return sorted(out, key=lambda p: [s.casefold() for s in p["path"]])

    @router.get("/api/content")
    def content(
        info: Me,
        q: Annotated[str | None, Query(max_length=200)] = None,
        project: Annotated[str | None, Query(max_length=100)] = None,
        kind: Kind | None = None,
    ) -> list[dict[str, Any]]:
        """Search and browse: permission-filtered, optionally within a project (and its nested ones)."""
        found = [*(items(info, "dashboard") if kind in (None, "dashboard") else [])]
        found += items(info, "model") if kind in (None, "model") else []
        if project is not None:
            found = [d for d in found if project in {p.id for p in ancestry(d["project"], site.projects)}]
        if q:
            terms = q.casefold().split()
            found = [
                d
                for d in found
                if all(
                    t
                    in " ".join(
                        [str(d["name"]), d["description"], " ".join(d["projectPath"]), d["owner"] or ""]
                    ).casefold()
                    for t in terms
                )
            ]
        return found

    @router.get("/api/home")
    def home(info: Me) -> dict[str, Any]:
        everything = {(d["kind"], d["id"]): d for d in items(info, "dashboard") + items(info, "model")}
        favorites = site.favorites(info.username) if hasattr(site, "favorites") else []
        recents = site.recents(info.username) if hasattr(site, "recents") else []
        updated = sorted(
            (d for d in everything.values() if d["updatedAt"]), key=lambda d: d["updatedAt"], reverse=True
        )
        return {
            "favorites": [everything[k] for k in favorites if k in everything],
            "recents": [everything[k] for k in recents if k in everything][:12],
            "recentlyUpdated": updated[:12],
        }

    @router.put("/api/favorites/{kind}/{item}", status_code=204)
    def add_favorite(kind: Kind, item: str, info: Me, _: Csrf) -> Response:
        visible_entry(info, kind, item)
        database().set_favorite(info.username, kind, item, True)
        return Response(status_code=204)

    @router.delete("/api/favorites/{kind}/{item}", status_code=204)
    def remove_favorite(kind: Kind, item: str, info: Me, _: Csrf) -> Response:
        database().set_favorite(info.username, kind, item, False)
        return Response(status_code=204)

    @router.get("/api/dashboards/{dashboard}/info")
    def dashboard_info(dashboard: str, info: Me) -> dict[str, Any]:
        """What the viewer can do here (which buttons to show) and the workbook's other tabs."""
        found = visible_entry(info, "dashboard", dashboard)
        mine = next((d for d in items(info, "dashboard") if d["id"] == dashboard), None)
        workbook = mine["workbook"] if mine else None
        tabs = (
            [
                {"id": d["id"], "name": d["name"]}
                for d in items(info, "dashboard")
                if d["workbook"] == workbook
            ]
            if workbook
            else []
        )
        return {
            **(mine or {}),
            "capabilities": capabilities(info.principal(), found, site.projects),
            "locked": controlling_project(ancestry(found.project, site.projects)) is not None,
            "tabs": tabs,
        }

    # ---- model browser -------------------------------------------------------------------------------

    @router.get("/api/models")
    def models(info: Me) -> list[dict[str, Any]]:
        return items(info, "model")

    @router.get("/api/models/{model}")
    def model(model: str, info: Me) -> dict[str, Any]:
        """Fields, hierarchies and parameters of a model the viewer may View (no connection details)."""
        found = visible_entry(info, "model", model)
        doc = site.models.get(model)
        if doc is None:
            raise HTTPException(404, f"no model {model!r}")
        principal = info.principal()
        fields = [
            {
                k: f[k]
                for k in (
                    "name",
                    "role",
                    "type",
                    "description",
                    "folder",
                    "formula",
                    "defaultAggregation",
                    "format",
                )
                if k in f
            }
            for f in doc.get("fields", [])
            if not f.get("hidden")
        ]
        out: dict[str, Any] = {
            "id": model,
            "title": doc.get("title", model),
            "description": doc.get("description", ""),
            "engine": (doc.get("connection") or {}).get("engine"),
            "fields": fields,
            "hierarchies": doc.get("hierarchies", []),
            "parameters": [
                {k: p[k] for k in ("name", "type", "default", "allowed") if k in p}
                for p in doc.get("parameters", [])
            ],
            "groups": [g.get("name") for g in doc.get("groups", [])],
            "sets": [s.get("name") for s in doc.get("sets", [])],
            "capabilities": capabilities(principal, found, site.projects),
            "dataVersion": found.data_version,
        }
        if evaluate(principal, Capability.OVERWRITE, found, site.projects).allowed:
            # Policy names (never the viewer-specific values) for those who may change the model.
            out["rls"] = [
                {"id": p["id"], "description": p.get("description", "")} for p in doc.get("rls", [])
            ]
        return out

    # ---- revisions -----------------------------------------------------------------------------------

    @router.get("/api/content/{kind}/{item}/revisions")
    def revisions(kind: Kind, item: str, info: Me) -> list[dict[str, Any]]:
        found = visible_entry(info, kind, item)
        if not evaluate(info.principal(), Capability.OVERWRITE, found, site.projects).allowed:
            raise HTTPException(403, "revision history needs Overwrite")
        return [
            {
                "number": r.number,
                "message": r.message,
                "createdBy": r.created_by,
                "createdAt": r.created_at.isoformat(),
                "sha256": r.sha256,
            }
            for r in database().revisions(kind, item)
        ]

    @router.post("/api/content/{kind}/{item}/revisions/{number}/restore")
    def restore(kind: Kind, item: str, number: int, info: Me, _: Csrf) -> dict[str, int]:
        found = visible_entry(info, kind, item)
        if not evaluate(info.principal(), Capability.OVERWRITE, found, site.projects).allowed:
            raise HTTPException(403, "restoring needs Overwrite")
        try:
            revision = database().restore(kind, item, number, by=info.username)
        except ContentError as exc:
            raise HTTPException(404, str(exc)) from None
        ctx.audit("content_restore", info.username, resource_kind=kind, resource_id=item, revision=number)
        return {"revision": revision}

    # ---- permissions dialog --------------------------------------------------------------------------

    def rules_json(rules: tuple[Rule, ...]) -> list[dict[str, Any]]:
        return [
            {
                "user": r.user,
                "group": r.group,
                "capabilities": {str(c): d for c, d in sorted(r.capabilities.items())},
            }
            for r in rules
        ]

    def resource(kind: str, item: str) -> ContentEntry | Project:
        if kind == "project":
            project = site.projects.get(item)
            if project is None:
                raise HTTPException(404, f"no project {item!r}")
            return project  # type: ignore[no-any-return]
        return entry(kind, item)

    def may_set_permissions(info: SessionInfo, target: ContentEntry | Project) -> bool:
        if isinstance(target, Project):
            return manages(info.principal(), target.id)
        return evaluate(info.principal(), Capability.SET_PERMISSIONS, target, site.projects).allowed

    @router.get("/api/permissions/{kind}/{item}")
    def permissions(kind: Literal["project", "dashboard", "model"], item: str, info: Me) -> dict[str, Any]:
        target = resource(kind, item)
        if not may_set_permissions(info, target):
            raise HTTPException(404, f"no {kind} {item!r}")
        if isinstance(target, Project):
            return {
                "kind": "project",
                "id": target.id,
                "assetPermissions": target.asset_permissions,
                "defaults": {ctype: rules_json(rules) for ctype, rules in target.defaults.items()},
                "capabilities": {k: [str(c) for c in tableau_order(v)] for k, v in APPLICABLE.items()},
            }
        controller = controlling_project(ancestry(target.project, site.projects))
        return {
            "kind": kind,
            "id": target.id,
            "rules": rules_json(target.rules),
            "lockedBy": controller.id if controller else None,
            "capabilities": {kind: [str(c) for c in tableau_order(APPLICABLE[kind])]},
        }

    @router.put("/api/permissions/{kind}/{item}", status_code=204)
    def set_permissions(
        kind: Literal["project", "dashboard", "model"], item: str, body: PermissionsBody, info: Me, _: Csrf
    ) -> Response:
        target = resource(kind, item)
        if not may_set_permissions(info, target):
            raise HTTPException(404, f"no {kind} {item!r}")
        if isinstance(target, ContentEntry) and controlling_project(ancestry(target.project, site.projects)):
            raise HTTPException(409, "the project's permissions are locked; change the project's defaults")
        if kind == "project" and body.contentType not in APPLICABLE:
            raise HTTPException(
                422, "project rules need a contentType: project, dashboard, model or connection"
            )
        allowed = set(APPLICABLE[body.contentType or kind]) | (
            {Capability.PROJECT_LEADER} if body.contentType == "project" else set()
        )
        try:
            rules = parse_rules(r.model_dump() for r in body.rules)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        for rule in rules:
            bad = set(rule.capabilities) - allowed
            if bad:
                raise HTTPException(422, f"not capabilities here: {', '.join(sorted(map(str, bad)))}")
            if rule.user is not None and rule.user not in site.users:
                raise HTTPException(422, f"unknown user {rule.user!r}")
        database().set_rules(kind, item, rules, content_type=body.contentType if kind == "project" else None)
        ctx.audit(
            "permission_change",
            info.username,
            resource_kind=kind,
            resource_id=item,
            contentType=body.contentType,
            rules=len(rules),
        )
        return Response(status_code=204)

    @router.get("/api/permissions/{kind}/{item}/effective")
    def effective(
        kind: Literal["project", "dashboard", "model"],
        item: str,
        info: Me,
        user: Annotated[str, Query(max_length=255)],
    ) -> dict[str, Any]:
        """Every capability for one user, with the reason (the dialog's "effective permissions")."""
        target = resource(kind, item)
        if not may_set_permissions(info, target):
            raise HTTPException(404, f"no {kind} {item!r}")
        who = site.users.get(user)
        if who is None:
            raise HTTPException(404, f"no user {user!r}")
        principal = Principal(who.username, who.groups, who.site_role, who.fullname)
        kind_caps = APPLICABLE["project" if isinstance(target, Project) else target.kind]
        return {
            "user": user,
            "siteRole": who.site_role.value,
            "capabilities": {
                str(c): {
                    "allowed": (v := evaluate(principal, c, target, site.projects)).allowed,
                    "reason": v.reason,
                }
                for c in tableau_order(kind_caps)
            },
        }

    # ---- projects ------------------------------------------------------------------------------------

    @router.post("/api/projects", status_code=201)
    def create_project(body: ProjectBody, info: Me, _: Csrf) -> dict[str, str]:
        if not _ID.match(body.id):
            raise HTTPException(422, "project ids are lower-case letters, digits and hyphens")
        if body.parent is None:
            require_admin(info)
        elif not manages(info.principal(), body.parent):
            raise HTTPException(
                403, "only administrators and the parent's owner or leaders add nested projects"
            )
        try:
            database().create_project(
                body.id, body.name, parent=body.parent, owner=info.username, description=body.description
            )
        except ContentError as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception as exc:  # unique violations differ between drivers
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(409, "a project with that id or name exists") from None
            raise
        ctx.audit("project_create", info.username, resource_kind="project", resource_id=body.id)
        return {"id": body.id}

    @router.patch("/api/projects/{project}", status_code=204)
    def update_project(project: str, body: ProjectPatch, info: Me, _: Csrf) -> Response:
        resource("project", project)
        if not manages(info.principal(), project):
            raise HTTPException(403, "only administrators and the project's owner or leaders")
        if body.owner is not None and body.owner not in site.users:
            raise HTTPException(422, f"unknown user {body.owner!r}")
        database().update_project(
            project,
            name=body.name,
            description=body.description,
            asset_permissions=body.assetPermissions,
            owner=body.owner,
        )
        ctx.audit("project_update", info.username, resource_kind="project", resource_id=project)
        return Response(status_code=204)

    @router.delete("/api/projects/{project}", status_code=204)
    def delete_project(project: str, info: Me, _: Csrf) -> Response:
        resource("project", project)
        if not manages(info.principal(), project):
            raise HTTPException(403, "only administrators and the project's owner or leaders")
        try:
            database().delete_project(project)
        except ContentError as exc:
            raise HTTPException(409, str(exc)) from None
        ctx.audit("project_delete", info.username, resource_kind="project", resource_id=project)
        return Response(status_code=204)

    # ---- site administration -------------------------------------------------------------------------

    @router.get("/api/admin/users")
    def admin_users(info: Me) -> list[dict[str, Any]]:
        require_admin(info)
        return list(database().admin_users())

    @router.post("/api/admin/users", status_code=201)
    def add_user(body: UserBody, info: Me, _: Csrf) -> dict[str, str]:
        require_admin(info)
        database().ensure_user(body.username, body.fullname, body.siteRole, email=body.email)
        ctx.audit("user_add", info.username, resource_id=body.username[:100], siteRole=body.siteRole.value)
        return {"username": body.username}

    @router.patch("/api/admin/users/{username}", status_code=204)
    def update_user(username: str, body: UserPatch, info: Me, _: Csrf) -> Response:
        require_admin(info)
        if username == info.username and (
            body.disabled or (body.siteRole and body.siteRole is not SiteRole.SITE_ADMINISTRATOR)
        ):
            raise HTTPException(409, "you cannot demote or disable yourself")
        try:
            database().set_user(
                username,
                site_role=body.siteRole,
                disabled=body.disabled,
                fullname=body.fullname,
                attributes=body.attributes,
            )
        except ContentError as exc:
            raise HTTPException(404, str(exc)) from None
        if (body.siteRole is not None or body.disabled) and hasattr(ctx.sessions, "delete_user_sessions"):
            ctx.sessions.delete_user_sessions(username)  # the new role applies at the next sign-in
        changed = sorted(k for k, v in body.model_dump().items() if v is not None)
        ctx.audit("user_update", info.username, resource_id=username[:100], changed=changed)
        return Response(status_code=204)

    @router.get("/api/admin/groups")
    def admin_groups(info: Me) -> list[dict[str, Any]]:
        require_admin(info)
        return list(database().admin_groups())

    @router.put("/api/admin/groups/{name}", status_code=204)
    def put_group(name: str, body: GroupBody, info: Me, _: Csrf) -> Response:
        require_admin(info)
        db = database()
        unknown = [u for u in body.members if u not in site.users]
        if unknown:
            raise HTTPException(422, f"unknown users: {', '.join(unknown[:5])}")
        db.ensure_group(name[:255], minimum_site_role=body.minimumSiteRole)
        db.set_members(name[:255], body.members)
        ctx.audit("group_update", info.username, resource_id=name[:100], members=len(body.members))
        return Response(status_code=204)

    @router.delete("/api/admin/groups/{name}", status_code=204)
    def delete_group(name: str, info: Me, _: Csrf) -> Response:
        require_admin(info)
        if not database().delete_group(name):
            raise HTTPException(404, f"no group {name!r}")
        ctx.audit("group_delete", info.username, resource_id=name[:100])
        return Response(status_code=204)

    @router.get("/api/admin/site")
    def admin_site(info: Me) -> dict[str, Any]:
        require_admin(info)
        return {
            "id": site.id,
            "name": site.name,
            "defaultSiteRole": site.default_role.value,
            "settings": database().settings(),
        }

    @router.patch("/api/admin/site", status_code=204)
    def update_site(body: SitePatch, info: Me, _: Csrf) -> Response:
        require_admin(info)
        if body.settings is not None:
            for key, value in body.settings.items():
                expected = _SITE_SETTINGS.get(key)
                if expected is None or not isinstance(value, expected) or isinstance(value, bool):
                    raise HTTPException(422, f"unknown or invalid site setting {key!r}")
                if key in _CHOICES and value not in _CHOICES[key]:
                    raise HTTPException(422, f"{key} is one of {', '.join(_CHOICES[key])}")
        database().update_site(name=body.name, default_site_role=body.defaultSiteRole, settings=body.settings)
        ctx.audit(
            "site_update",
            info.username,
            changed=sorted(k for k, v in body.model_dump().items() if v is not None),
        )
        return Response(status_code=204)

    # ---- old Tableau URLs (P3.9) ----------------------------------------------------------------------

    @router.get("/api/redirects/resolve")
    def resolve(info: Me, path: Annotated[str, Query(max_length=2048)]) -> dict[str, str]:
        """Where an old Tableau link now lives, if the viewer may see it (404 otherwise)."""
        from tabfree_api.redirects import normalise

        key = normalise(path)
        target = site.resolve_legacy(key) if key else None
        if target is None:
            raise HTTPException(404, "no dashboard for that link")
        kind, item = target
        visible_entry(info, kind, item)
        return {"to": f"/{'dashboards' if kind == 'dashboard' else 'models'}/{item}"}

    @router.get("/api/admin/legacy-urls")
    def legacy_urls(info: Me) -> list[dict[str, str]]:
        require_admin(info)
        return list(database().legacy_urls())

    @router.put("/api/admin/legacy-urls", status_code=204)
    def set_legacy_url(body: LegacyUrlBody, info: Me, _: Csrf) -> Response:
        """Record an old Tableau view URL (the migration factory calls this for every view)."""
        from tabfree_api.redirects import normalise

        require_admin(info)
        key = normalise(body.path)
        if key is None:
            raise HTTPException(422, "not a Tableau view URL (…/views/<workbook>/<sheet>)")
        entry(body.kind, body.id)
        database().set_legacy_url(key, body.kind, body.id)
        ctx.audit("redirect_set", info.username, resource_kind=body.kind, resource_id=body.id)
        return Response(status_code=204)

    @router.get("/api/admin/audit")
    def audit(
        info: Me,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        before: int | None = None,
        actor: Annotated[str | None, Query(max_length=255)] = None,
        action: Annotated[str | None, Query(max_length=40)] = None,
        resource: Annotated[str | None, Query(max_length=100)] = None,
    ) -> list[dict[str, Any]]:
        """The audit log, newest first (P3.8)."""
        require_admin(info)
        if ctx.audit_log is None:
            raise HTTPException(501, "this needs the metadata database (TABFREE_DATABASE_URL)")
        return list(
            ctx.audit_log.events(limit=limit, before=before, actor=actor, action=action, resource_id=resource)
        )
