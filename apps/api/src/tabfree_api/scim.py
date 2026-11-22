"""SCIM 2.0 provisioning (BUILD_PLAN P6.9; RFC 7643 and RFC 7644): users and groups from the IdP.

The identity provider (Okta, Entra ID, OneLogin...) calls ``/scim/v2`` with a bearer token: a personal
access token of a site administrator (create one for a dedicated provisioning account). Supported:

- ``/Users``: list (``filter=userName eq "..."``, ``startIndex``, ``count``), get, create, replace,
  patch (``active``, ``name``, ``emails``, ``displayName``), delete (deprovisions: the account is
  disabled, its content kept, as Tableau does when a user owns content);
- ``/Groups``: list (``filter=displayName eq "..."``), get, create, replace, patch (add, remove or
  replace ``members``; rename), delete;
- ``/ServiceProviderConfig``, ``/ResourceTypes``, ``/Schemas``.

Users are created with the site's default role, or ``urn:tabfree:scim:User`` ``siteRole``. Groups created
here are marked as coming from the IdP. Errors use the SCIM error schema.
"""

import re
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import SiteRole
from tabfree_api.db import schema as t

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
EXTENSION = "urn:tabfree:scim:User"
RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
MEDIA = "application/scim+json"
_FILTER = re.compile(r'^\s*(userName|displayName|externalId)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.I)


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type


def error_response(exc: ScimError) -> JSONResponse:
    body: dict[str, Any] = {"schemas": [ERROR], "status": str(exc.status), "detail": exc.detail}
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return JSONResponse(body, status_code=exc.status, media_type=MEDIA)


def _filter(text: str | None) -> tuple[str, str] | None:
    if not text:
        return None
    match = _FILTER.match(text)
    if match is None:
        raise ScimError(400, "only 'attribute eq \"value\"' filters are supported", "invalidFilter")
    return match.group(1).lower(), match.group(2).replace('\\"', '"')


def _email(resource: Mapping[str, Any]) -> str | None:
    emails = resource.get("emails") or []
    primary = next((e for e in emails if isinstance(e, Mapping) and e.get("primary")), None)
    first = primary or next((e for e in emails if isinstance(e, Mapping)), None)
    return str(first["value"]) if first and first.get("value") else None


def _fullname(resource: Mapping[str, Any]) -> str | None:
    name = resource.get("name") or {}
    if isinstance(name, Mapping):
        if name.get("formatted"):
            return str(name["formatted"])
        parts = [name.get("givenName"), name.get("familyName")]
        if any(parts):
            return " ".join(str(p) for p in parts if p)
    return str(resource["displayName"]) if resource.get("displayName") else None


def add_routes(router: APIRouter, ctx: Any, current: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias

    def site(info: SessionInfo) -> Any:
        if info.site_role is not SiteRole.SITE_ADMINISTRATOR:
            raise HTTPException(403, "SCIM needs a site administrator's token")
        if not hasattr(ctx.site, "engine"):
            raise HTTPException(404, "SCIM needs the database (TABFREE_DATABASE_URL)")
        return ctx.site

    def base(request: Request) -> str:
        return f"{ctx.settings.base_url.rstrip('/')}/scim/v2"

    def reply(body: Any, status: int = 200) -> JSONResponse:
        return JSONResponse(body, status_code=status, media_type=MEDIA)

    # ---- users ---------------------------------------------------------------------------------------
    def user_rows(s: Any) -> list[dict[str, Any]]:
        u = t.users.c
        with s.engine.connect() as conn:
            rows = conn.execute(
                select(u.id, u.username, u.fullname, u.email, u.disabled, t.site_users.c.site_role)
                .join(t.site_users, t.site_users.c.user_id == u.id)
                .where(t.site_users.c.site_id == s.id)
                .order_by(u.username)
            ).mappings()
            return [dict(r) for r in rows]

    def user_doc(s: Any, row: Mapping[str, Any], request: Request) -> dict[str, Any]:
        groups = sorted(s.users[row["username"]].groups) if row["username"] in s.users else []
        doc: dict[str, Any] = {
            "schemas": [USER, EXTENSION],
            "id": row["id"],
            "userName": row["username"],
            "name": {"formatted": row["fullname"] or row["username"]},
            "displayName": row["fullname"] or row["username"],
            "active": not row["disabled"],
            "groups": [{"display": g} for g in groups],
            EXTENSION: {"siteRole": row["site_role"]},
            "meta": {"resourceType": "User", "location": f"{base(request)}/Users/{row['id']}"},
        }
        if row["email"]:
            doc["emails"] = [{"value": row["email"], "primary": True}]
        return doc

    def find_user(s: Any, user_id: str) -> dict[str, Any]:
        row = next((r for r in user_rows(s) if r["id"] == user_id), None)
        if row is None:
            raise ScimError(404, f"no user {user_id}")
        return row

    def apply_user(s: Any, username: str, resource: Mapping[str, Any]) -> None:
        role = (resource.get(EXTENSION) or {}).get("siteRole")
        try:
            site_role = SiteRole(role) if role else None
        except ValueError:
            raise ScimError(400, f"unknown site role {role!r}", "invalidValue") from None
        active = resource.get("active")
        s.set_user(
            username,
            site_role=site_role,
            disabled=(not active) if isinstance(active, bool) else None,
            fullname=_fullname(resource),
        )
        email = _email(resource)
        if email:
            s.ensure_user(username, email=email)

    @router.get("/scim/v2/Users")
    def list_users(
        request: Request,
        info: Me,
        filter: str | None = None,
        start_index: Annotated[int, Query(alias="startIndex")] = 1,
        count: int = 100,
    ) -> Any:
        s = site(info)
        rows = user_rows(s)
        wanted = _filter(filter)
        if wanted is not None:
            attribute, value = wanted
            if attribute != "username":
                raise ScimError(400, "Users filter on userName only", "invalidFilter")
            rows = [r for r in rows if r["username"].lower() == value.lower()]
        start, size = max(1, start_index), max(0, min(count, 500))
        page = rows[start - 1 : start - 1 + size]
        return reply(
            {"schemas": [LIST], "totalResults": len(rows), "startIndex": start, "itemsPerPage": len(page),
             "Resources": [user_doc(s, r, request) for r in page]}
        )  # fmt: skip

    @router.get("/scim/v2/Users/{user_id}")
    def get_user(user_id: str, request: Request, info: Me) -> Any:
        s = site(info)
        return reply(user_doc(s, find_user(s, user_id), request))

    @router.post("/scim/v2/Users")
    async def create_user(request: Request, info: Me) -> Any:
        s = site(info)
        resource = await request.json()
        username = str(resource.get("userName") or "").strip()
        if not username or len(username) > 255:
            raise ScimError(400, "userName is required", "invalidValue")
        if any(r["username"].lower() == username.lower() for r in user_rows(s)):
            raise ScimError(409, f"user {username!r} exists", "uniqueness")
        s.ensure_user(
            username, _fullname(resource) or username, None, email=_email(resource), auth_source="idp"
        )
        apply_user(s, username, resource)
        ctx.audit("scim_user_create", info.username, resource_kind="user", resource_id=username)
        row = next(r for r in user_rows(s) if r["username"] == username)
        return reply(user_doc(s, row, request), 201)

    @router.put("/scim/v2/Users/{user_id}")
    async def replace_user(user_id: str, request: Request, info: Me) -> Any:
        s = site(info)
        row = find_user(s, user_id)
        apply_user(s, row["username"], await request.json())
        return reply(user_doc(s, find_user(s, user_id), request))

    @router.patch("/scim/v2/Users/{user_id}")
    async def patch_user(user_id: str, request: Request, info: Me) -> Any:
        s = site(info)
        row = find_user(s, user_id)
        body = await request.json()
        if PATCH not in (body.get("schemas") or []):
            raise ScimError(400, "expected a PatchOp", "invalidSyntax")
        resource: dict[str, Any] = {}
        for op in body.get("Operations") or []:
            kind = str(op.get("op", "")).lower()
            if kind not in ("replace", "add"):
                raise ScimError(400, f"unsupported operation {kind!r} on users", "invalidSyntax")
            path, value = op.get("path"), op.get("value")
            if path is None and isinstance(value, Mapping):
                resource.update(value)
            elif path in ("active", "displayName", "name", "emails"):
                resource[str(path)] = value
            elif path in ("name.formatted", "name.givenName", "name.familyName"):
                resource.setdefault("name", {})[str(path).split(".", 1)[1]] = value
            elif path == f"{EXTENSION}:siteRole":
                resource[EXTENSION] = {"siteRole": value}
            else:
                raise ScimError(400, f"unsupported path {path!r}", "invalidPath")
        apply_user(s, row["username"], resource)
        ctx.audit("scim_user_update", info.username, resource_kind="user", resource_id=row["username"])
        return reply(user_doc(s, find_user(s, user_id), request))

    @router.delete("/scim/v2/Users/{user_id}", status_code=204)
    def delete_user(user_id: str, info: Me) -> Response:
        s = site(info)
        row = find_user(s, user_id)
        s.set_user(row["username"], disabled=True)  # deprovisioned; their content stays
        ctx.audit("scim_user_deprovision", info.username, resource_kind="user", resource_id=row["username"])
        return Response(status_code=204)

    # ---- groups --------------------------------------------------------------------------------------
    def group_rows(s: Any) -> list[dict[str, Any]]:
        g = t.groups.c
        with s.engine.connect() as conn:
            rows = conn.execute(
                select(g.id, g.name, g.source).where(g.site_id == s.id).order_by(g.name)
            ).mappings()
            groups = [dict(r) for r in rows]
            members = conn.execute(
                select(t.group_members.c.group_id, t.users.c.id, t.users.c.username).join(
                    t.users, t.users.c.id == t.group_members.c.user_id
                )
            ).all()
        for group in groups:
            group["members"] = [(uid, name) for gid, uid, name in members if gid == group["id"]]
        return groups

    def group_doc(group: Mapping[str, Any], request: Request) -> dict[str, Any]:
        return {
            "schemas": [GROUP],
            "id": group["id"],
            "displayName": group["name"],
            "members": [{"value": uid, "display": name} for uid, name in group["members"]],
            "meta": {"resourceType": "Group", "location": f"{base(request)}/Groups/{group['id']}"},
        }

    def find_group(s: Any, group_id: str) -> dict[str, Any]:
        group = next((g for g in group_rows(s) if g["id"] == group_id), None)
        if group is None:
            raise ScimError(404, f"no group {group_id}")
        return group

    def usernames(s: Any, members: Any) -> list[str]:
        ids = {r["id"]: r["username"] for r in user_rows(s)}
        out = []
        for member in members or []:
            value = str(member.get("value", "")) if isinstance(member, Mapping) else str(member)
            if value not in ids:
                raise ScimError(400, f"no user {value}", "invalidValue")
            out.append(ids[value])
        return out

    @router.get("/scim/v2/Groups")
    def list_groups(
        request: Request,
        info: Me,
        filter: str | None = None,
        start_index: Annotated[int, Query(alias="startIndex")] = 1,
        count: int = 100,
    ) -> Any:
        s = site(info)
        groups = group_rows(s)
        wanted = _filter(filter)
        if wanted is not None:
            if wanted[0] != "displayname":
                raise ScimError(400, "Groups filter on displayName only", "invalidFilter")
            groups = [g for g in groups if g["name"].lower() == wanted[1].lower()]
        start, size = max(1, start_index), max(0, min(count, 500))
        page = groups[start - 1 : start - 1 + size]
        return reply(
            {"schemas": [LIST], "totalResults": len(groups), "startIndex": start, "itemsPerPage": len(page),
             "Resources": [group_doc(g, request) for g in page]}
        )  # fmt: skip

    @router.get("/scim/v2/Groups/{group_id}")
    def get_group(group_id: str, request: Request, info: Me) -> Any:
        return reply(group_doc(find_group(site(info), group_id), request))

    @router.post("/scim/v2/Groups")
    async def create_group(request: Request, info: Me) -> Any:
        s = site(info)
        resource = await request.json()
        name = str(resource.get("displayName") or "").strip()
        if not name or len(name) > 255:
            raise ScimError(400, "displayName is required", "invalidValue")
        if any(g["name"].lower() == name.lower() for g in group_rows(s)):
            raise ScimError(409, f"group {name!r} exists", "uniqueness")
        s.ensure_group(name, source="idp")
        s.set_members(name, usernames(s, resource.get("members")))
        ctx.audit("scim_group_create", info.username, resource_kind="group", resource_id=name)
        group = next(g for g in group_rows(s) if g["name"] == name)
        return reply(group_doc(group, request), 201)

    @router.put("/scim/v2/Groups/{group_id}")
    async def replace_group(group_id: str, request: Request, info: Me) -> Any:
        s = site(info)
        group = find_group(s, group_id)
        resource = await request.json()
        name = _rename(s, group, resource.get("displayName"))
        s.set_members(name, usernames(s, resource.get("members")))
        return reply(group_doc(find_group(s, group_id), request))

    def _rename(s: Any, group: Mapping[str, Any], name: Any) -> str:
        if not name or name == group["name"]:
            return str(group["name"])
        from sqlalchemy import update

        with s.engine.begin() as conn:
            conn.execute(update(t.groups).where(t.groups.c.id == group["id"]).values(name=str(name)[:255]))
        s.invalidate()
        return str(name)

    @router.patch("/scim/v2/Groups/{group_id}")
    async def patch_group(group_id: str, request: Request, info: Me) -> Any:
        s = site(info)
        group = find_group(s, group_id)
        body = await request.json()
        if PATCH not in (body.get("schemas") or []):
            raise ScimError(400, "expected a PatchOp", "invalidSyntax")
        members = [name for _uid, name in group["members"]]
        name = str(group["name"])
        for op in body.get("Operations") or []:
            kind, path, value = str(op.get("op", "")).lower(), op.get("path"), op.get("value")
            if path == "displayName" or (
                path is None and isinstance(value, Mapping) and "displayName" in value
            ):
                name = _rename(s, {**group, "name": name}, value if path else value["displayName"])
            elif path == "members" or (path is None and isinstance(value, Mapping) and "members" in value):
                given = usernames(s, value if path else value["members"])
                if kind == "add":
                    members += [u for u in given if u not in members]
                elif kind == "remove":
                    members = [u for u in members if u not in given] if given else []
                elif kind == "replace":
                    members = given
                else:
                    raise ScimError(400, f"unsupported operation {kind!r}", "invalidSyntax")
            elif kind == "remove" and isinstance(path, str) and path.startswith("members[value eq "):
                target = re.findall(r'"([^"]+)"', path)
                drop = set(usernames(s, [{"value": v} for v in target]))
                members = [u for u in members if u not in drop]
            else:
                raise ScimError(400, f"unsupported path {path!r}", "invalidPath")
        s.set_members(name, members)
        ctx.audit("scim_group_update", info.username, resource_kind="group", resource_id=name)
        return reply(group_doc(find_group(s, group_id), request))

    @router.delete("/scim/v2/Groups/{group_id}", status_code=204)
    def delete_group(group_id: str, info: Me) -> Response:
        s = site(info)
        group = find_group(s, group_id)
        s.delete_group(group["name"])
        ctx.audit("scim_group_delete", info.username, resource_kind="group", resource_id=group["name"])
        return Response(status_code=204)

    # ---- discovery -----------------------------------------------------------------------------------
    @router.get("/scim/v2/ServiceProviderConfig")
    def service_provider_config() -> Any:
        return reply(
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "patch": {"supported": True},
                "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                "filter": {"supported": True, "maxResults": 500},
                "changePassword": {"supported": False},
                "sort": {"supported": False},
                "etag": {"supported": False},
                "authenticationSchemes": [
                    {
                        "type": "oauthbearertoken",
                        "name": "Bearer token",
                        "description": "A site administrator's personal access token",
                    }
                ],
            }
        )

    @router.get("/scim/v2/ResourceTypes")
    def resource_types() -> Any:
        return reply(
            {
                "schemas": [LIST],
                "totalResults": 2,
                "Resources": [
                    {
                        "schemas": [RESOURCE_TYPE],
                        "id": "User",
                        "name": "User",
                        "endpoint": "/Users",
                        "schema": USER,
                        "schemaExtensions": [{"schema": EXTENSION, "required": False}],
                    },
                    {
                        "schemas": [RESOURCE_TYPE],
                        "id": "Group",
                        "name": "Group",
                        "endpoint": "/Groups",
                        "schema": GROUP,
                    },
                ],
            }
        )

    @router.get("/scim/v2/Schemas")
    def schemas() -> Any:
        return reply(
            {"schemas": [LIST], "totalResults": 3, "Resources": [
                {"id": USER, "name": "User"}, {"id": GROUP, "name": "Group"},
                {"id": EXTENSION, "name": "TabFree user",
                 "attributes": [{"name": "siteRole", "type": "string"}]},
            ]}
        )  # fmt: skip


__all__ = ["ScimError", "add_routes", "error_response"]
