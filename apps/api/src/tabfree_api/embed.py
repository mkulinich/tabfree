"""Embedding (BUILD_PLAN P6.8): connected apps, embed tokens, ``frame-ancestors`` and a JS embed API.

How it works, as with Tableau's connected apps:

1. An administrator registers a **connected app**: its client id, a secret id and secret (shown once),
   the origins allowed to frame dashboards, and optionally the projects it may embed from.
2. The host application signs a short-lived **JWT** (HS256) for a signed-in person::

       header  {"alg": "HS256", "kid": <secret id>, "iss": <client id>}
       claims  {"iss": <client id>, "aud": "tabfree", "sub": <username>, "jti": <unique>,
                "exp": <now + at most 10 minutes>, "scp": ["tabfree:views:embed"],
                "region": "East"}          # any other string claim is a user attribute for RLS

3. Its page loads ``/embed.js`` and calls ``TabFree.embed(element, {server, dashboard, token})``. The
   frame (``/embed/<dashboard>``) exchanges the token, once (``jti``), for a **limited session** that
   may only read that dashboard and run its queries (``tabfree.embed:<dashboard>``), as that person,
   with their permissions and row-level security plus the token's attributes.
4. The embed page is served with ``Content-Security-Policy: frame-ancestors`` listing the connected
   apps' origins, and talks to its parent only through ``postMessage`` with those origins: the parent
   may set filters and parameters, and hears state changes and selections.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from importlib import resources
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import Capability, SiteRole, ancestry
from tabfree_api.db import schema as t
from tabfree_api.db.store import now

AUDIENCE = "tabfree"
EMBED_SCOPES = {"tabfree:views:embed", "tableau:views:embed"}
RESERVED = {"iss", "aud", "sub", "jti", "exp", "iat", "nbf", "scp", "kid"}
_ORIGIN = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?$|^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$")
SESSION_S = 3600


class EmbedError(ValueError):
    pass


class AppBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domains: list[str] = Field(min_length=1, max_length=50)
    projects: list[str] = Field(default_factory=list, max_length=100)


class SessionBody(BaseModel):
    token: str = Field(min_length=10, max_length=8000)
    dashboard: str = Field(max_length=100)


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign_token(secret: str, header: dict[str, Any], claims: dict[str, Any]) -> str:
    """HS256 JWT (what a host application does; used by tests and the docs)."""
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT", **header}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(mac)}"


def check_origin(origin: str) -> str:
    origin = origin.strip().rstrip("/")
    if not _ORIGIN.match(origin):
        raise EmbedError(f"{origin!r} is not an origin (https://host[:port])")
    return origin


def verify_token(
    apps: dict[str, dict[str, Any]], token: str, *, max_s: int, clock: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the connected app, the claims) of a valid embed token, or EmbedError. ``jti`` is checked later."""
    try:
        head_b64, body_b64, mac_b64 = token.split(".")
        header = json.loads(_b64decode(head_b64))
        claims = json.loads(_b64decode(body_b64))
        mac = _b64decode(mac_b64)
    except (ValueError, TypeError):
        raise EmbedError("malformed token") from None
    if header.get("alg") != "HS256":
        raise EmbedError("embed tokens are HS256")
    app = next((a for a in apps.values() if a["secret_id"] == header.get("kid")), None)
    if app is None or not app["enabled"]:
        raise EmbedError("unknown or disabled connected app")
    expected = hmac.new(app["secret"].encode(), f"{head_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise EmbedError("bad signature")
    if claims.get("iss") != app["id"] or header.get("iss", app["id"]) != app["id"]:
        raise EmbedError("the issuer is not this connected app")
    audience = claims.get("aud")
    if audience != AUDIENCE and not (isinstance(audience, list) and AUDIENCE in audience):
        raise EmbedError(f"the audience must be {AUDIENCE!r}")
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or exp <= clock:
        raise EmbedError("the token has expired")
    if exp - clock > max_s + 30:
        raise EmbedError(f"embed tokens may live at most {max_s} seconds")
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("jti"), str) or not claims["jti"]:
        raise EmbedError("sub and jti are required")
    scopes = claims.get("scp") or []
    if not isinstance(scopes, list) or not EMBED_SCOPES & set(scopes):
        raise EmbedError("the token lacks the embed scope (tabfree:views:embed)")
    return app, claims


def attributes(claims: dict[str, Any]) -> dict[str, str]:
    """Extra string claims → user attributes for row-level security (at most 20, short values)."""
    out: dict[str, str] = {}
    for key, value in claims.items():
        if key in RESERVED or not isinstance(value, str | int | float) or isinstance(value, bool):
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$", key):
            continue
        out[key] = str(value)[:256]
        if len(out) == 20:
            break
    return out


def add_routes(router: APIRouter, ctx: Any, current: Any, csrf: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806

    def db() -> Any:
        if not hasattr(ctx.site, "engine"):
            raise HTTPException(404, "embedding needs the database (TABFREE_DATABASE_URL)")
        return ctx.site

    def apps() -> dict[str, dict[str, Any]]:
        a = t.connected_apps.c
        with db().engine.connect() as conn:
            rows = conn.execute(select(t.connected_apps).where(a.site_id == ctx.site.id)).mappings()
            return {r["id"]: dict(r) for r in rows}

    def origins() -> list[str]:
        return sorted({d for a in apps().values() if a["enabled"] for d in a["domains"] or []})

    def admin(info: SessionInfo) -> None:
        if info.site_role is not SiteRole.SITE_ADMINISTRATOR:
            raise HTTPException(403, "site administrators only")

    def public(app: dict[str, Any]) -> dict[str, Any]:
        return {
            k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in app.items()
            if k not in ("secret", "site_id")
        }

    @router.get("/api/connected-apps")
    def list_apps(info: Me) -> list[dict[str, Any]]:
        admin(info)
        return [public(a) for a in apps().values()]

    @router.post("/api/connected-apps", status_code=201)
    def create_app(body: AppBody, info: Me, _: Csrf) -> dict[str, Any]:
        admin(info)
        try:
            domains = [check_origin(d) for d in body.domains]
        except EmbedError as exc:
            raise HTTPException(422, str(exc)) from None
        unknown = [p for p in body.projects if p not in ctx.site.projects]
        if unknown:
            raise HTTPException(422, f"no project {unknown[0]!r}")
        app_id, secret_id, secret = str(uuid.uuid4()), str(uuid.uuid4()), secrets.token_urlsafe(32)
        with db().engine.begin() as conn:
            conn.execute(
                insert(t.connected_apps).values(
                    site_id=ctx.site.id, id=app_id, name=body.name, secret_id=secret_id, secret=secret,
                    enabled=True, domains=domains, projects=body.projects, created_at=now(),
                )
            )  # fmt: skip
        ctx.audit("connected_app_create", info.username, resource_kind="connected_app", resource_id=app_id)
        return {"id": app_id, "secretId": secret_id, "secret": secret, "domains": domains}

    @router.post("/api/connected-apps/{app_id}/rotate")
    def rotate(app_id: str, info: Me, _: Csrf) -> dict[str, str]:
        admin(info)
        secret_id, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        a = t.connected_apps.c
        with db().engine.begin() as conn:
            done = conn.execute(
                update(t.connected_apps)
                .where(a.site_id == ctx.site.id, a.id == app_id)
                .values(secret_id=secret_id, secret=secret)
            )
        if done.rowcount != 1:
            raise HTTPException(404, "no such connected app")
        ctx.audit("connected_app_rotate", info.username, resource_kind="connected_app", resource_id=app_id)
        return {"secretId": secret_id, "secret": secret}

    @router.delete("/api/connected-apps/{app_id}", status_code=204)
    def delete_app(app_id: str, info: Me, _: Csrf) -> None:
        admin(info)
        a = t.connected_apps.c
        with db().engine.begin() as conn:
            if (
                conn.execute(
                    delete(t.connected_apps).where(a.site_id == ctx.site.id, a.id == app_id)
                ).rowcount
                != 1
            ):
                raise HTTPException(404, "no such connected app")

    @router.post("/api/embed/session")
    def embed_session(body: SessionBody) -> dict[str, Any]:
        """Exchange an embed token, once, for a session limited to one dashboard."""
        site = db()
        clock = time.time()
        try:
            app, claims = verify_token(apps(), body.token, max_s=ctx.settings.embed_token_max_s, clock=clock)
        except EmbedError as exc:
            ctx.audit("embed_session", None, "deny", reason=str(exc))
            raise HTTPException(401, f"embed token refused: {exc}") from None
        user = site.users.get(claims["sub"])
        if user is None:
            raise HTTPException(401, "embed token refused: no such user")
        j = t.embed_jtis.c
        jti = f"{app['id']}:{claims['jti']}"[:100]
        import datetime as dt

        with site.engine.begin() as conn:
            conn.execute(delete(t.embed_jtis).where(j.expires_at < now()))
            reused = conn.execute(select(j.jti).where(j.jti == jti)).first() is not None
            if not reused:
                expires = dt.datetime.fromtimestamp(float(claims["exp"]), dt.UTC)
                conn.execute(insert(t.embed_jtis).values(jti=jti, expires_at=expires))
        if reused:
            ctx.audit("embed_session", user.username, "deny", reason="token reused")
            raise HTTPException(401, "embed token refused: already used")
        entry = site.dashboards.get(body.dashboard)
        principal_attrs = {**user.attributes, **attributes(claims)}
        info = SessionInfo(
            user.username,
            user.fullname,
            frozenset(user.groups),
            user.site_role,
            clock + 60,
            email=user.email,
            attributes=principal_attrs,
        )
        if entry is None or not site.can(info.principal(), Capability.VIEW, body.dashboard):
            raise HTTPException(404, f"no dashboard {body.dashboard!r}")
        allowed = app["projects"] or []
        if allowed and not {p.id for p in ancestry(entry.project, site.projects)} & set(allowed):
            raise HTTPException(403, "this connected app may not embed that dashboard")
        token = ctx.sessions.create(
            user.username, user.fullname, frozenset(user.groups), user.site_role, SESSION_S,
            email=user.email, attributes=principal_attrs,
            scopes=frozenset({f"tabfree.embed:{body.dashboard}"}),
        )  # fmt: skip
        ctx.audit(
            "embed_session",
            user.username,
            resource_kind="dashboard",
            resource_id=body.dashboard,
            app=app["id"],
        )
        return {"session": token, "expiresIn": SESSION_S, "origins": app["domains"]}

    @router.get("/embed/{dashboard}", include_in_schema=False)
    def embed_page(dashboard: str) -> HTMLResponse:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", dashboard):
            raise HTTPException(404)
        allowed = origins()
        nonce = secrets.token_urlsafe(16)
        ancestors = " ".join(["'self'", *allowed])
        csp = (
            f"default-src 'none'; script-src 'self' 'nonce-{nonce}'; style-src 'unsafe-inline'; "
            f"connect-src 'self'; frame-src 'self' blob: {ctx.settings.usercontent_origin or ''}; "
            f"img-src 'self' data: blob:; frame-ancestors {ancestors}; base-uri 'none'; form-action 'none'"
        )
        page = EMBED_PAGE.replace("__NONCE__", nonce).replace("__ORIGINS__", json.dumps(allowed))
        return HTMLResponse(
            page,
            headers={
                "Content-Security-Policy": csp,
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )

    @router.get("/embed.js", include_in_schema=False)
    def embed_js() -> Response:
        return Response(
            EMBED_JS, media_type="text/javascript", headers={"Cache-Control": "public, max-age=300"}
        )


#: The page inside the host application's frame (``static/embed.html``) and the embed API for host
#: pages (``static/embed.js``). The token arrives in the URL fragment, never sent to servers or
#: logged, and is exchanged once; the session token stays in memory.
EMBED_PAGE = resources.files("tabfree_api").joinpath("static", "embed.html").read_text(encoding="utf-8")
EMBED_JS = resources.files("tabfree_api").joinpath("static", "embed.js").read_text(encoding="utf-8")
