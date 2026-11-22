"""Content lifecycle (BUILD_PLAN P6.6): recycle bin, move, copy, change owner, certification, warnings.

No ``from __future__ import annotations``: FastAPI evaluates the ``Depends`` annotations.

Who may do what follows Tableau:
- **delete** needs Delete; deleted content waits in the **recycle bin** (``content.deleted_at``) for
  ``RETENTION_DAYS``. Its owner, project leaders and administrators may restore it; administrators
  may purge it for good;
- **move** needs Move on the content and Publish on the target project;
- **copy** (Save As) needs Download Workbook on the dashboard and Publish on the target project; the
  copy starts with the target project's default rules and its creator as owner;
- **change owner**: administrators, and the project's owner or leaders;
- **certify** and set a **data quality warning**: administrators and project leaders. Viewers see the
  certification and the warning wherever the content appears.
"""

import datetime as dt
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, select, update

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import Capability, ContentEntry, Principal, SiteRole, ancestry, evaluate
from tabfree_api.content import ContentError
from tabfree_api.db import schema as t
from tabfree_api.db.store import now

RETENTION_DAYS = 30
WARNING_TYPES = ("warning", "deprecated", "stale", "under_maintenance", "sensitive")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
Kind = Literal["dashboard", "model"]


class MoveBody(BaseModel):
    project: str = Field(max_length=100)


class CopyBody(BaseModel):
    id: str = Field(max_length=100)
    project: str = Field(max_length=100)
    title: str | None = Field(default=None, max_length=200)


class OwnerBody(BaseModel):
    owner: str = Field(max_length=255)


class CertificationBody(BaseModel):
    certified: bool
    note: str = Field(default="", max_length=2000)


class WarningBody(BaseModel):
    type: Literal["warning", "deprecated", "stale", "under_maintenance", "sensitive"]
    message: str = Field(default="", max_length=2000)


def _aware(value: Any) -> Any:
    return value.replace(tzinfo=dt.UTC) if value is not None and value.tzinfo is None else value


def add_routes(router: APIRouter, ctx: Any, current: Any, csrf: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806

    def site() -> Any:
        if not hasattr(ctx.site, "engine"):
            raise HTTPException(501, "this needs the metadata database (TABFREE_DATABASE_URL)")
        return ctx.site

    def key(kind: str, item: str) -> Any:
        c = t.content.c
        return and_(c.site_id == ctx.site.id, c.kind == kind, c.id == item)

    def entry(info: SessionInfo, kind: str, item: str) -> ContentEntry:
        s = site()
        found = (s.dashboards if kind == "dashboard" else s.model_entries).get(item)
        if found is None or not evaluate(info.principal(), Capability.VIEW, found, s.projects).allowed:
            raise HTTPException(404, f"no {kind} {item!r}")
        return found  # type: ignore[no-any-return]

    def can(info: SessionInfo, capability: Capability, found: ContentEntry) -> bool:
        return evaluate(info.principal(), capability, found, ctx.site.projects).allowed

    def leads(principal: Principal, project: str | None) -> bool:
        if principal.site_role is SiteRole.SITE_ADMINISTRATOR:
            return True
        return any(
            p.owner == principal.username or p.leads(principal) for p in ancestry(project, ctx.site.projects)
        )

    def may_publish_to(info: SessionInfo, project_id: str) -> None:
        project = ctx.site.projects.get(project_id)
        if (
            project is None
            or not evaluate(info.principal(), Capability.VIEW, project, ctx.site.projects).allowed
        ):
            raise HTTPException(404, f"no project {project_id!r}")
        if not evaluate(info.principal(), Capability.PUBLISH, project, ctx.site.projects).allowed:
            raise HTTPException(403, f"publishing to {project.name!r} needs the Publish capability")

    # ---- recycle bin ---------------------------------------------------------------------------------
    @router.delete("/api/content/{kind}/{item}", status_code=204)
    def delete_content(kind: Kind, item: str, info: Me, _: Csrf) -> None:
        found = entry(info, kind, item)
        if not can(info, Capability.DELETE, found):
            raise HTTPException(403, "deleting needs the Delete capability")
        site().soft_delete(kind, item)
        ctx.audit("content_delete", info.username, resource_kind=kind, resource_id=item)

    @router.get("/api/recycle-bin")
    def recycle_bin(info: Me) -> list[dict[str, Any]]:
        s = site()
        c = t.content.c
        principal = info.principal()
        with s.engine.connect() as conn:
            rows = conn.execute(
                select(c.kind, c.id, c.name, c.project_id, c.deleted_at, t.users.c.username)
                .outerjoin(t.users, t.users.c.id == c.owner_id)
                .where(c.site_id == s.id, c.deleted_at.is_not(None))
                .order_by(c.deleted_at.desc())
            ).all()
        out = []
        for kind, item, name, project, deleted, owner in rows:
            if owner != info.username and not leads(principal, project):
                continue
            when = _aware(deleted)
            out.append(
                {
                    "kind": kind,
                    "id": item,
                    "name": name,
                    "project": project,
                    "owner": owner,
                    "deletedAt": when.isoformat(),
                    "purgeAfter": (when + dt.timedelta(days=RETENTION_DAYS)).isoformat(),
                }
            )
        return out

    def deleted_row(kind: str, item: str) -> Any:
        c = t.content.c
        with site().engine.connect() as conn:
            row = conn.execute(
                select(c.project_id, c.deleted_at, t.users.c.username)
                .outerjoin(t.users, t.users.c.id == c.owner_id)
                .where(key(kind, item), c.deleted_at.is_not(None))
            ).first()
        if row is None:
            raise HTTPException(404, f"no deleted {kind} {item!r}")
        return row

    @router.post("/api/recycle-bin/{kind}/{item}/restore")
    def undelete(kind: Kind, item: str, info: Me, _: Csrf) -> dict[str, str]:
        project, _deleted, owner = deleted_row(kind, item)
        if owner != info.username and not leads(info.principal(), project):
            raise HTTPException(403, "only the owner, a project leader or an administrator restores it")
        with site().engine.begin() as conn:
            conn.execute(update(t.content).where(key(kind, item)).values(deleted_at=None, updated_at=now()))
        ctx.site.invalidate()
        ctx.audit("content_undelete", info.username, resource_kind=kind, resource_id=item)
        return {"status": "restored"}

    @router.delete("/api/recycle-bin/{kind}/{item}", status_code=204)
    def purge(kind: Kind, item: str, info: Me, _: Csrf) -> None:
        if info.site_role is not SiteRole.SITE_ADMINISTRATOR:
            raise HTTPException(403, "site administrators only")
        deleted_row(kind, item)
        purge_content(site(), kind, item)
        ctx.audit("content_purge", info.username, resource_kind=kind, resource_id=item)

    # ---- move, copy, owner ---------------------------------------------------------------------------
    @router.post("/api/content/{kind}/{item}/move")
    def move(kind: Kind, item: str, body: MoveBody, info: Me, _: Csrf) -> dict[str, str]:
        found = entry(info, kind, item)
        if not can(info, Capability.MOVE, found):
            raise HTTPException(403, "moving needs the Move capability")
        may_publish_to(info, body.project)
        site().move(kind, item, body.project)
        ctx.audit("content_move", info.username, resource_kind=kind, resource_id=item, project=body.project)
        return {"project": body.project}

    @router.post("/api/content/dashboard/{item}/copy", status_code=201)
    def copy(item: str, body: CopyBody, info: Me, _: Csrf) -> dict[str, Any]:
        found = entry(info, "dashboard", item)
        if not can(info, Capability.DOWNLOAD_WORKBOOK, found):
            raise HTTPException(403, "saving a copy needs Download Workbook")
        may_publish_to(info, body.project)
        if not _SLUG.match(body.id):
            raise HTTPException(422, "dashboard ids are lowercase letters, digits and hyphens")
        s = site()
        c = t.content.c
        with s.engine.connect() as conn:
            if conn.execute(select(c.id).where(key("dashboard", body.id))).first() is not None:
                raise HTTPException(409, f"{body.id!r} exists (or is in the recycle bin)")
        current = s.revision("dashboard", item)
        document = {
            **current.document,
            "id": body.id,
            "title": body.title or f"{current.document.get('title', item)} (copy)",
        }
        revision = s.publish(
            "dashboard", body.id, document, files=current.files, project=body.project, owner=info.username,
            message=f"copy of {item} revision {current.number}", by=info.username,
            origin={"system": "tabfree", "copyOf": item, "revision": current.number},
        )  # fmt: skip
        ctx.audit(
            "content_publish",
            info.username,
            resource_kind="dashboard",
            resource_id=body.id,
            revision=revision,
        )
        return {"id": body.id, "revision": revision}

    @router.post("/api/content/{kind}/{item}/owner")
    def change_owner(kind: Kind, item: str, body: OwnerBody, info: Me, _: Csrf) -> dict[str, str]:
        found = entry(info, kind, item)
        if not leads(info.principal(), found.project):
            raise HTTPException(403, "changing the owner needs a project leader or an administrator")
        s = site()
        if body.owner not in s.users:
            raise HTTPException(422, f"no user {body.owner!r}")
        with s.engine.begin() as conn:
            uid = conn.execute(select(t.users.c.id).where(t.users.c.username == body.owner)).scalar_one()
            conn.execute(update(t.content).where(key(kind, item)).values(owner_id=uid, updated_at=now()))
        s.invalidate()
        ctx.audit("content_owner", info.username, resource_kind=kind, resource_id=item, owner=body.owner)
        return {"owner": body.owner}

    # ---- certification and data quality warnings ------------------------------------------------------
    @router.post("/api/content/{kind}/{item}/certification")
    def certify(kind: Kind, item: str, body: CertificationBody, info: Me, _: Csrf) -> dict[str, Any]:
        found = entry(info, kind, item)
        if not leads(info.principal(), found.project):
            raise HTTPException(403, "certifying needs a project leader or an administrator")
        with site().engine.begin() as conn:
            conn.execute(
                update(t.content)
                .where(key(kind, item))
                .values(
                    certified=body.certified,
                    certification_note=body.note if body.certified else "",
                    certified_by=info.username if body.certified else None,
                )
            )
        ctx.audit(
            "content_certify", info.username, resource_kind=kind, resource_id=item, certified=body.certified
        )
        return quality(kind, item)

    @router.put("/api/content/{kind}/{item}/quality-warning")
    def set_warning(kind: Kind, item: str, body: WarningBody, info: Me, _: Csrf) -> dict[str, Any]:
        found = entry(info, kind, item)
        if not leads(info.principal(), found.project) and found.owner != info.username:
            raise HTTPException(403, "warnings are set by the owner, a project leader or an administrator")
        warning = {"type": body.type, "message": body.message, "by": info.username, "at": now().isoformat()}
        with site().engine.begin() as conn:
            conn.execute(update(t.content).where(key(kind, item)).values(quality_warning=warning))
        ctx.audit("content_warning", info.username, resource_kind=kind, resource_id=item, type=body.type)
        return quality(kind, item)

    @router.delete("/api/content/{kind}/{item}/quality-warning", status_code=204)
    def clear_warning(kind: Kind, item: str, info: Me, _: Csrf) -> None:
        found = entry(info, kind, item)
        if not leads(info.principal(), found.project) and found.owner != info.username:
            raise HTTPException(
                403, "warnings are cleared by the owner, a project leader or an administrator"
            )
        with site().engine.begin() as conn:
            conn.execute(update(t.content).where(key(kind, item)).values(quality_warning=None))

    @router.get("/api/content/{kind}/{item}/quality")
    def get_quality(kind: Kind, item: str, info: Me) -> dict[str, Any]:
        entry(info, kind, item)
        return quality(kind, item)

    def quality(kind: str, item: str) -> dict[str, Any]:
        c = t.content.c
        with site().engine.connect() as conn:
            row = conn.execute(
                select(c.certified, c.certification_note, c.certified_by, c.quality_warning).where(
                    key(kind, item)
                )
            ).first()
        if row is None:
            raise HTTPException(404, f"no {kind} {item!r}")
        return {
            "certified": bool(row[0]),
            "certificationNote": row[1],
            "certifiedBy": row[2],
            "warning": row[3],
        }


def purge_content(site: Any, kind: str, item: str) -> None:
    """Remove deleted content, its revisions, rules and user state, for good."""
    with site.engine.begin() as conn:
        for table, column in (
            (t.content_revisions, "content_id"),
            (t.favorites, "content_id"),
            (t.content_tags, "content_id"),
        ):
            conn.execute(
                delete(table).where(
                    table.c.site_id == site.id, table.c.kind == kind, getattr(table.c, column) == item
                )
            )
        conn.execute(
            delete(t.permission_rules).where(
                t.permission_rules.c.site_id == site.id,
                t.permission_rules.c.resource_kind == kind,
                t.permission_rules.c.resource_id == item,
            )
        )
        if kind == "dashboard":
            for table in (t.custom_views, t.comments, t.share_links, t.subscriptions, t.data_alerts):
                conn.execute(delete(table).where(table.c.site_id == site.id, table.c.dashboard_id == item))
        conn.execute(
            delete(t.content).where(
                t.content.c.site_id == site.id, t.content.c.kind == kind, t.content.c.id == item
            )
        )
    site.invalidate()


def purge_expired(site: Any, *, days: int = RETENTION_DAYS) -> int:
    """Purge everything in the recycle bin for longer than ``days`` (run by ``jobs prune``)."""
    c = t.content.c
    limit = now() - dt.timedelta(days=days)
    with site.engine.connect() as conn:
        rows = conn.execute(select(c.kind, c.id).where(c.site_id == site.id, c.deleted_at < limit)).all()
    for kind, item in rows:
        purge_content(site, kind, item)
    return len(rows)


__all__ = ["RETENTION_DAYS", "WARNING_TYPES", "ContentError", "add_routes", "purge_content", "purge_expired"]
