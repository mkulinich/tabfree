"""User state (BUILD_PLAN P6.5): custom views, sharing links, comments, tags and collections.

No ``from __future__ import annotations``: FastAPI evaluates the ``Depends`` annotations.

- **Custom views** save a dashboard's filter and parameter state under a name. They are private or
  shared (everyone who can view the dashboard sees shared ones); anyone may make a view their own
  default. Saving a shared view needs Share Customized, as in Tableau.
- **Sharing links** are short ids for a dashboard in a state (``/s/<id>``). They grant nothing: the
  person who opens one still needs View.
- **Comments** need View Comments to read and Add Comments to write. ``@username`` mentions email
  the mentioned people who can view the dashboard (never the comment's data, only a link).
- **Tags** are set by people who may overwrite the content; anyone sees tags on content they can view.
- **Collections** are personal lists of dashboards and models, optionally shared; their items are
  filtered by the viewer's permissions, so sharing a collection never shares content.
"""

import re
import secrets
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, insert, or_, select, update

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import Capability, ContentEntry, SiteRole, evaluate
from tabfree_api.db import schema as t
from tabfree_api.db.store import now

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{1,64})")
TAG = re.compile(r"^[\w .-]{1,50}$")
MAX_STATE_CHARS = 20_000
Kind = Literal["dashboard", "model"]


class ViewBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    state: dict[str, Any] = Field(default_factory=dict)
    shared: bool = False
    default: bool = False


class ViewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    state: dict[str, Any] | None = None
    shared: bool | None = None


class ShareBody(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    state: dict[str, Any] | None = None


class TagsBody(BaseModel):
    tags: list[str] = Field(max_length=30)


class CollectionItem(BaseModel):
    kind: Kind
    id: str = Field(max_length=100)


class CollectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    shared: bool = False
    items: list[CollectionItem] = Field(default_factory=list, max_length=500)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _row(row: Any) -> dict[str, Any]:
    return {k: _iso(v) for k, v in dict(row).items() if k != "site_id"}


def add_routes(router: APIRouter, ctx: Any, current: Any, csrf: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806

    def site() -> Any:
        if not hasattr(ctx.site, "engine"):
            raise HTTPException(501, "this needs the metadata database (TABFREE_DATABASE_URL)")
        return ctx.site

    def found(info: SessionInfo, kind: str, item: str) -> ContentEntry:
        s = site()
        entry = (s.dashboards if kind == "dashboard" else s.model_entries).get(item)
        if entry is None or not evaluate(info.principal(), Capability.VIEW, entry, s.projects).allowed:
            raise HTTPException(404, f"no {kind} {item!r}")
        return entry  # type: ignore[no-any-return]

    def can(info: SessionInfo, capability: Capability, entry: ContentEntry) -> bool:
        return evaluate(info.principal(), capability, entry, ctx.site.projects).allowed

    def check_state(state: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(state)) > MAX_STATE_CHARS:
            raise HTTPException(422, "the saved state is too large")
        unknown = set(state) - {"filters", "parameters", "selections"}
        if unknown:
            raise HTTPException(422, f"unknown state keys: {', '.join(sorted(unknown))}")
        return state

    # ---- custom views --------------------------------------------------------------------------------
    @router.get("/api/dashboards/{dashboard}/views")
    def list_views(dashboard: str, info: Me) -> dict[str, Any]:
        found(info, "dashboard", dashboard)
        s = site()
        c = t.custom_views.c
        with s.engine.connect() as conn:
            rows = conn.execute(
                select(t.custom_views)
                .where(
                    c.site_id == s.id, c.dashboard_id == dashboard, or_(c.owner == info.username, c.shared)
                )
                .order_by(c.name)
            ).mappings()
            views = [_row(r) for r in rows]
            d = t.custom_view_defaults.c
            default = conn.execute(
                select(d.custom_view_id).where(
                    d.site_id == s.id, d.username == info.username, d.dashboard_id == dashboard
                )
            ).scalar()
        if default not in {v["id"] for v in views}:
            default = None
        return {"views": views, "default": default}

    @router.post("/api/dashboards/{dashboard}/views", status_code=201)
    def save_view(dashboard: str, body: ViewBody, info: Me, _: Csrf) -> dict[str, Any]:
        entry = found(info, "dashboard", dashboard)
        if body.shared and not can(info, Capability.SHARE_CUSTOMIZED, entry):
            raise HTTPException(403, "sharing a custom view needs Share Customized")
        s = site()
        vid, when = str(uuid.uuid4()), now()
        with s.engine.begin() as conn:
            conn.execute(
                insert(t.custom_views).values(
                    site_id=s.id, id=vid, dashboard_id=dashboard, owner=info.username, name=body.name,
                    state=check_state(body.state), shared=body.shared, created_at=when, updated_at=when,
                )
            )  # fmt: skip
        if body.default:
            set_default(dashboard, vid, info)
        return {"id": vid}

    def view_row(info: SessionInfo, view_id: str, *, write: bool) -> dict[str, Any]:
        s = site()
        c = t.custom_views.c
        with s.engine.connect() as conn:
            row = (
                conn.execute(select(t.custom_views).where(c.site_id == s.id, c.id == view_id))
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(404, "no such custom view")
        found(info, "dashboard", row["dashboard_id"])
        mine = row["owner"] == info.username or info.site_role is SiteRole.SITE_ADMINISTRATOR
        if (write and not mine) or (not write and not mine and not row["shared"]):
            raise HTTPException(404, "no such custom view")
        return dict(row)

    @router.patch("/api/custom-views/{view_id}")
    def update_view(view_id: str, body: ViewPatch, info: Me, _: Csrf) -> dict[str, Any]:
        row = view_row(info, view_id, write=True)
        values: dict[str, Any] = {"updated_at": now()}
        if body.name is not None:
            values["name"] = body.name
        if body.state is not None:
            values["state"] = check_state(body.state)
        if body.shared is not None:
            if body.shared and not can(
                info, Capability.SHARE_CUSTOMIZED, found(info, "dashboard", row["dashboard_id"])
            ):
                raise HTTPException(403, "sharing a custom view needs Share Customized")
            values["shared"] = body.shared
        c = t.custom_views.c
        with site().engine.begin() as conn:
            conn.execute(
                update(t.custom_views).where(c.site_id == ctx.site.id, c.id == view_id).values(**values)
            )
        return {"id": view_id}

    @router.delete("/api/custom-views/{view_id}", status_code=204)
    def delete_view(view_id: str, info: Me, _: Csrf) -> None:
        view_row(info, view_id, write=True)
        s = site()
        with s.engine.begin() as conn:
            conn.execute(
                delete(t.custom_views).where(t.custom_views.c.site_id == s.id, t.custom_views.c.id == view_id)
            )
            conn.execute(
                delete(t.custom_view_defaults).where(
                    t.custom_view_defaults.c.site_id == s.id,
                    t.custom_view_defaults.c.custom_view_id == view_id,
                )
            )

    def set_default(dashboard: str, view_id: str | None, info: SessionInfo) -> None:
        s = site()
        d = t.custom_view_defaults.c
        where = and_(d.site_id == s.id, d.username == info.username, d.dashboard_id == dashboard)
        with s.engine.begin() as conn:
            conn.execute(delete(t.custom_view_defaults).where(where))
            if view_id is not None:
                conn.execute(
                    insert(t.custom_view_defaults).values(
                        site_id=s.id, username=info.username, dashboard_id=dashboard, custom_view_id=view_id
                    )
                )

    @router.put("/api/custom-views/{view_id}/default")
    def make_default(view_id: str, info: Me, _: Csrf) -> dict[str, str]:
        row = view_row(info, view_id, write=False)
        set_default(row["dashboard_id"], view_id, info)
        return {"default": view_id}

    @router.delete("/api/dashboards/{dashboard}/views/default", status_code=204)
    def clear_default(dashboard: str, info: Me, _: Csrf) -> None:
        found(info, "dashboard", dashboard)
        set_default(dashboard, None, info)

    # ---- sharing links -------------------------------------------------------------------------------
    @router.post("/api/dashboards/{dashboard}/share", status_code=201)
    def share(dashboard: str, body: ShareBody, info: Me, _: Csrf) -> dict[str, str]:
        found(info, "dashboard", dashboard)
        s = site()
        link = secrets.token_urlsafe(12)
        with s.engine.begin() as conn:
            conn.execute(
                insert(t.share_links).values(
                    site_id=s.id, id=link, dashboard_id=dashboard, state=check_state(body.state),
                    created_by=info.username, created_at=now(),
                )
            )  # fmt: skip
        return {"id": link, "url": f"{ctx.settings.base_url.rstrip('/')}/s/{link}"}

    @router.get("/api/share/{link}")
    def open_share(link: str, info: Me) -> dict[str, Any]:
        s = site()
        c = t.share_links.c
        with s.engine.connect() as conn:
            row = conn.execute(select(c.dashboard_id, c.state).where(c.site_id == s.id, c.id == link)).first()
        if row is None:
            raise HTTPException(404, "this link does not exist")
        found(info, "dashboard", row[0])  # the link grants nothing
        return {"dashboard": row[0], "state": row[1]}

    @router.get("/s/{link}", include_in_schema=False)
    def share_redirect(link: str) -> RedirectResponse:
        # The portal resolves the link after sign-in, so permissions are checked for the viewer.
        return RedirectResponse(f"/?share={link}", status_code=302)

    # ---- comments ------------------------------------------------------------------------------------
    @router.get("/api/dashboards/{dashboard}/comments")
    def list_comments(dashboard: str, info: Me) -> list[dict[str, Any]]:
        entry = found(info, "dashboard", dashboard)
        if not can(info, Capability.VIEW_COMMENTS, entry):
            raise HTTPException(403, "reading comments needs View Comments")
        c = t.comments.c
        with site().engine.connect() as conn:
            rows = conn.execute(
                select(t.comments)
                .where(c.site_id == ctx.site.id, c.dashboard_id == dashboard, c.deleted_at.is_(None))
                .order_by(c.created_at)
            ).mappings()
            return [_row(r) for r in rows]

    @router.post("/api/dashboards/{dashboard}/comments", status_code=201)
    def add_comment(dashboard: str, body: CommentBody, info: Me, _: Csrf) -> dict[str, Any]:
        entry = found(info, "dashboard", dashboard)
        if not can(info, Capability.ADD_COMMENTS, entry):
            raise HTTPException(403, "commenting needs Add Comments")
        s = site()
        mentioned = sorted({m for m in MENTION.findall(body.body) if m in s.users and m != info.username})
        cid = str(uuid.uuid4())
        with s.engine.begin() as conn:
            conn.execute(
                insert(t.comments).values(
                    site_id=s.id, id=cid, dashboard_id=dashboard, author=info.username, body=body.body,
                    mentions=mentioned, state=check_state(body.state) if body.state else None,
                    created_at=now(),
                )
            )  # fmt: skip
        notified = notify_mentions(info, dashboard, mentioned)
        ctx.audit("comment_create", info.username, resource_kind="dashboard", resource_id=dashboard)
        return {"id": cid, "mentions": mentioned, "notified": notified}

    def notify_mentions(info: SessionInfo, dashboard: str, mentioned: list[str]) -> list[str]:
        from tabfree_api.jobs.common import session_info
        from tabfree_api.jobs.mail import Message

        if ctx.mailer is None:
            return []
        title = str(ctx.site.bundle(dashboard).manifest.get("title", dashboard))
        link = f"{ctx.settings.base_url.rstrip('/')}/dashboards/{dashboard}"
        out = []
        for username in mentioned:
            user = ctx.site.users[username]
            entry = ctx.site.dashboards[dashboard]
            other = session_info(ctx.site, username)
            if not user.email or not can(other, Capability.VIEW_COMMENTS, entry):
                continue
            ctx.mailer.send(
                Message(
                    to=[user.email],
                    subject=f"{info.fullname or info.username} mentioned you on {title}",
                    text=f"{info.fullname or info.username} mentioned you in a comment on {title}.\n\n"
                    f"{link}\n",
                )
            )
            out.append(username)
        return out

    @router.delete("/api/comments/{comment_id}", status_code=204)
    def delete_comment(comment_id: str, info: Me, _: Csrf) -> None:
        s = site()
        c = t.comments.c
        with s.engine.connect() as conn:
            row = conn.execute(
                select(c.author, c.dashboard_id).where(c.site_id == s.id, c.id == comment_id)
            ).first()
        if row is None:
            raise HTTPException(404, "no such comment")
        entry = found(info, "dashboard", row[1])
        if (
            row[0] != info.username
            and info.site_role is not SiteRole.SITE_ADMINISTRATOR
            and entry.owner != info.username
        ):
            raise HTTPException(
                403, "only the author, the dashboard's owner or an administrator deletes a comment"
            )
        with s.engine.begin() as conn:
            conn.execute(
                update(t.comments).where(c.site_id == s.id, c.id == comment_id).values(deleted_at=now())
            )

    # ---- tags ----------------------------------------------------------------------------------------
    @router.get("/api/content/{kind}/{item}/tags")
    def get_tags(kind: Kind, item: str, info: Me) -> list[str]:
        found(info, kind, item)
        g = t.content_tags.c
        with site().engine.connect() as conn:
            rows = conn.execute(
                select(g.tag)
                .where(g.site_id == ctx.site.id, g.kind == kind, g.content_id == item)
                .order_by(g.tag)
            )
            return [r[0] for r in rows]

    @router.put("/api/content/{kind}/{item}/tags")
    def set_tags(kind: Kind, item: str, body: TagsBody, info: Me, _: Csrf) -> list[str]:
        entry = found(info, kind, item)
        if not can(info, Capability.OVERWRITE, entry):
            raise HTTPException(403, "tagging needs Overwrite")
        tags = sorted({tag.strip().lower() for tag in body.tags if tag.strip()})
        bad = [tag for tag in tags if not TAG.match(tag)]
        if bad:
            raise HTTPException(
                422, f"tags are up to 50 letters, digits, spaces, dots and hyphens: {bad[0]!r}"
            )
        g = t.content_tags.c
        with site().engine.begin() as conn:
            conn.execute(
                delete(t.content_tags).where(g.site_id == ctx.site.id, g.kind == kind, g.content_id == item)
            )
            for tag in tags:
                conn.execute(
                    insert(t.content_tags).values(site_id=ctx.site.id, kind=kind, content_id=item, tag=tag)
                )
        return tags

    @router.get("/api/tags/{tag}")
    def tagged(tag: str, info: Me) -> list[dict[str, str]]:
        s = site()
        g = t.content_tags.c
        with s.engine.connect() as conn:
            rows = conn.execute(
                select(g.kind, g.content_id).where(g.site_id == s.id, g.tag == tag.lower())
            ).all()
        out = []
        principal = info.principal()
        for kind, item in rows:
            entry = (s.dashboards if kind == "dashboard" else s.model_entries).get(item)
            if entry is not None and evaluate(principal, Capability.VIEW, entry, s.projects).allowed:
                out.append({"kind": kind, "id": item})
        return out

    # ---- collections ---------------------------------------------------------------------------------
    def visible_items(info: SessionInfo, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        s = ctx.site
        principal = info.principal()
        out = []
        for i in items:
            entry = (s.dashboards if i["kind"] == "dashboard" else s.model_entries).get(i["id"])
            if entry is not None and evaluate(principal, Capability.VIEW, entry, s.projects).allowed:
                out.append(i)
        return out

    @router.get("/api/collections")
    def list_collections(info: Me) -> list[dict[str, Any]]:
        s = site()
        c = t.collections.c
        with s.engine.connect() as conn:
            rows = conn.execute(
                select(t.collections)
                .where(c.site_id == s.id, or_(c.owner == info.username, c.shared))
                .order_by(c.name)
            ).mappings()
            out = [_row(r) for r in rows]
        for row in out:
            row["items"] = visible_items(info, row["items"] or [])
        return out

    @router.post("/api/collections", status_code=201)
    def create_collection(body: CollectionBody, info: Me, _: Csrf) -> dict[str, str]:
        s = site()
        cid, when = str(uuid.uuid4()), now()
        items = visible_items(info, [i.model_dump() for i in body.items])  # only what the owner can see
        with s.engine.begin() as conn:
            conn.execute(
                insert(t.collections).values(
                    site_id=s.id, id=cid, owner=info.username, name=body.name, description=body.description,
                    shared=body.shared, items=items, created_at=when, updated_at=when,
                )
            )  # fmt: skip
        return {"id": cid}

    @router.put("/api/collections/{collection_id}")
    def update_collection(collection_id: str, body: CollectionBody, info: Me, _: Csrf) -> dict[str, str]:
        s = site()
        c = t.collections.c
        where = and_(c.site_id == s.id, c.id == collection_id, c.owner == info.username)
        items = visible_items(info, [i.model_dump() for i in body.items])
        with s.engine.begin() as conn:
            done = conn.execute(
                update(t.collections).where(where).values(
                    name=body.name, description=body.description, shared=body.shared, items=items,
                    updated_at=now(),
                )
            )  # fmt: skip
        if done.rowcount != 1:
            raise HTTPException(404, "no such collection of yours")
        return {"id": collection_id}

    @router.delete("/api/collections/{collection_id}", status_code=204)
    def delete_collection(collection_id: str, info: Me, _: Csrf) -> None:
        s = site()
        c = t.collections.c
        with s.engine.begin() as conn:
            done = conn.execute(
                delete(t.collections).where(
                    c.site_id == s.id, c.id == collection_id, c.owner == info.username
                )
            )
        if done.rowcount != 1:
            raise HTTPException(404, "no such collection of yours")
