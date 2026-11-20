"""Sign-in routes, the governed dashboard API and a minimal portal page (P1.13).

No ``from __future__ import annotations`` here: FastAPI must evaluate the ``Depends`` annotations,
which name functions local to :func:`create_router`.

Every API call needs a session. Dashboards are listed and served only when the viewer has the View
capability; queries run through :class:`QueryService` with the viewer's identity, so row-level
security comes from the semantic model. State-changing requests must carry the
``X-TabFree-Request`` header, which a cross-site form or image cannot send (CSRF defence).
"""

import datetime as dt
import html
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from tabfree_api.auth import (
    FLOW_COOKIE,
    SESSION_COOKIE,
    AuthError,
    OidcClient,
    SessionInfo,
    SessionStore,
    safe_return_to,
)
from tabfree_api.authz import Capability, SiteRole
from tabfree_api.content import ContentError, Site
from tabfree_api.engines import EngineError
from tabfree_api.service import QueryService
from tabfree_api.settings import Settings
from tabfree_compiler import CompileError, Session

log = logging.getLogger("tabfree.api")


class Context:
    def __init__(
        self,
        settings: Settings,
        site: Site,
        sessions: SessionStore,
        service: QueryService,
        oidc: OidcClient | None,
        today: dt.date | None = None,
    ) -> None:
        self.settings, self.site, self.sessions, self.service, self.oidc = (
            settings,
            site,
            sessions,
            service,
            oidc,
        )
        self.today = today


class QueryBody(BaseModel):
    query: str = Field(max_length=64)
    state: dict[str, Any]
    selections: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class DomainBody(BaseModel):
    filter: str = Field(max_length=64)
    state: dict[str, Any]


def create_router(ctx: Context) -> APIRouter:
    router = APIRouter()
    settings = ctx.settings

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(settings.session_hours * 3600),
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            path="/",
        )

    def current(tabfree_session: Annotated[str | None, Cookie()] = None) -> SessionInfo:
        info = ctx.sessions.get(tabfree_session)
        if info is None:
            raise HTTPException(401, "sign in first")
        return info

    def csrf(x_tabfree_request: Annotated[str | None, Header()] = None) -> None:
        if x_tabfree_request != "1":
            raise HTTPException(403, "missing the X-TabFree-Request header")

    def viewer_session(info: SessionInfo) -> Session:
        return Session(username=info.username, fullname=info.fullname, groups=info.groups, today=ctx.today)

    def require_view(info: SessionInfo, dashboard: str) -> None:
        # 404, not 403, so viewers cannot probe which dashboards exist.
        if not ctx.site.can(info.principal(), Capability.VIEW, dashboard):
            raise HTTPException(404, f"no dashboard {dashboard!r}")

    # ---- sign-in -------------------------------------------------------------------------------

    @router.get("/auth/login", include_in_schema=False)
    def login(return_to: Annotated[str | None, Query(alias="return")] = None) -> Response:
        if ctx.oidc is None:
            if settings.dev_login:
                return RedirectResponse("/auth/dev", status_code=303)
            raise HTTPException(
                503, "sign-in is not configured (set TABFREE_OIDC_ISSUER and client settings)"
            )
        state, nonce, verifier = ctx.sessions.start_flow(safe_return_to(return_to))
        try:
            url = ctx.oidc.authorize_url(state, nonce, verifier)
        except AuthError as exc:
            raise HTTPException(502, str(exc)) from None
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            FLOW_COOKIE,
            state,
            max_age=600,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            path="/auth",
        )
        return response

    @router.get("/auth/callback", include_in_schema=False)
    def callback(
        state: str = "",
        code: str = "",
        error: str | None = None,
        tabfree_oidc_state: Annotated[str | None, Cookie()] = None,
    ) -> Response:
        if ctx.oidc is None:
            raise HTTPException(404)
        if error:
            raise HTTPException(401, "the identity provider did not sign you in")
        if not state or not tabfree_oidc_state or state != tabfree_oidc_state:
            raise HTTPException(400, "this sign-in did not start in this browser")
        flow = ctx.sessions.finish_flow(state)
        if flow is None:
            raise HTTPException(400, "this sign-in has expired; try again")
        nonce, verifier, return_to = flow
        try:
            identity = ctx.oidc.exchange(code, nonce, verifier)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from None
        site_user = ctx.site.users.get(identity.username)
        role = site_user.site_role if site_user else ctx.site.default_role
        token = ctx.sessions.create(
            identity.username, identity.fullname, identity.groups, role, settings.session_hours * 3600
        )
        log.info("sign-in user=%s via oidc", identity.username)
        response = RedirectResponse(return_to, status_code=303)
        set_session_cookie(response, token)
        response.delete_cookie(FLOW_COOKIE, path="/auth")
        return response

    @router.get("/auth/dev", response_class=HTMLResponse, include_in_schema=False)
    def dev_page() -> HTMLResponse:
        if not settings.dev_login:
            raise HTTPException(404)
        items = "".join(
            '<li><form method="post" action="/auth/dev">'
            f'<input type="hidden" name="user" value="{html.escape(u.username)}">'
            f"<button>{html.escape(u.fullname)}</button> <small>{u.site_role.value}, "
            f"{html.escape(', '.join(sorted(u.groups)) or 'no groups')}</small></form></li>"
            for u in ctx.site.users.values()
        )
        return HTMLResponse(
            _shell("Sign in (development)", f"<p>Development sign-in: no password.</p><ul>{items}</ul>")
        )

    @router.post("/auth/dev", include_in_schema=False)
    async def dev_login(request: Request) -> Response:
        if not settings.dev_login:
            raise HTTPException(404)
        form = (await request.body()).decode()
        user = dict(pair.split("=", 1) for pair in form.split("&") if "=" in pair).get("user", "")
        site_user = ctx.site.users.get(user)
        if site_user is None:
            raise HTTPException(400, "unknown user")
        token = ctx.sessions.create(
            site_user.username,
            site_user.fullname,
            site_user.groups,
            site_user.site_role,
            settings.session_hours * 3600,
        )
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        return response

    @router.post("/auth/logout", include_in_schema=False)
    def logout(
        _: Annotated[None, Depends(csrf)], tabfree_session: Annotated[str | None, Cookie()] = None
    ) -> Response:
        ctx.sessions.delete(tabfree_session)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ---- API -----------------------------------------------------------------------------------

    @router.get("/api/me")
    def me(info: Annotated[SessionInfo, Depends(current)]) -> dict[str, Any]:
        return {
            "username": info.username,
            "fullname": info.fullname,
            "groups": sorted(info.groups),
            "siteRole": info.site_role.value,
        }

    @router.get("/api/dashboards")
    def dashboards(info: Annotated[SessionInfo, Depends(current)]) -> list[dict[str, str]]:
        return ctx.site.visible(info.principal())

    @router.get("/api/dashboards/{dashboard}")
    def bundle(dashboard: str, info: Annotated[SessionInfo, Depends(current)]) -> dict[str, Any]:
        require_view(info, dashboard)
        b = ctx.site.bundle(dashboard)
        return {"manifest": b.manifest, "html": b.html}

    @router.post("/api/dashboards/{dashboard}/query")
    def query(
        dashboard: str,
        body: QueryBody,
        info: Annotated[SessionInfo, Depends(current)],
        _: Annotated[None, Depends(csrf)],
    ) -> dict[str, Any]:
        require_view(info, dashboard)
        try:
            return ctx.service.query(
                ctx.site.bundle(dashboard).manifest,
                body.query,
                body.state,
                body.selections,
                viewer_session(info),
            )
        except (CompileError, ContentError) as exc:
            raise HTTPException(400, str(exc)) from None
        except EngineError as exc:
            raise HTTPException(502, str(exc)) from None

    @router.post("/api/dashboards/{dashboard}/domain")
    def domain(
        dashboard: str,
        body: DomainBody,
        info: Annotated[SessionInfo, Depends(current)],
        _: Annotated[None, Depends(csrf)],
    ) -> dict[str, Any]:
        require_view(info, dashboard)
        try:
            return ctx.service.domain(
                ctx.site.bundle(dashboard).manifest, body.filter, body.state, viewer_session(info)
            )
        except (CompileError, ContentError) as exc:
            raise HTTPException(400, str(exc)) from None
        except EngineError as exc:
            raise HTTPException(502, str(exc)) from None

    @router.get("/runtime/{name}", include_in_schema=False)
    def runtime(name: str) -> FileResponse:
        if name not in ("host.js", "frame.js"):
            raise HTTPException(404)
        path = settings.runtime_dir / name
        if not path.is_file():
            raise HTTPException(503, "build the runtime first: npm run build -w @tabfree/runtime")
        return FileResponse(path, media_type="text/javascript")

    # ---- portal --------------------------------------------------------------------------------

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(tabfree_session: Annotated[str | None, Cookie()] = None) -> Response:
        info = ctx.sessions.get(tabfree_session)
        if info is None:
            return HTMLResponse(_shell("TabFree", '<p><a href="/auth/login">Sign in</a></p>'))
        return HTMLResponse(
            _PORTAL.replace(
                "__ME__",
                json.dumps({"name": info.fullname, "admin": info.site_role is SiteRole.SITE_ADMINISTRATOR}),
            )
        )

    return router


def _shell(title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>:root{color-scheme:light dark;font:14px/1.5 system-ui,sans-serif}body{margin:24px}"
        "li{margin:6px 0}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )


_PORTAL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TabFree</title>
<style>
  :root { color-scheme: light dark; font: 14px/1.4 system-ui, sans-serif; }
  body { margin: 0; background: Canvas; color: CanvasText; }
  header { display: flex; gap: 16px; align-items: center; padding: 10px 16px;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }
  header h1 { font-size: 15px; margin: 0 auto 0 0; }
  #empty { padding: 24px; }
</style>
</head>
<body>
<header>
  <h1>TabFree</h1>
  <select id="dashboard" aria-label="Dashboard"></select>
  <span id="me"></span>
  <button id="logout">Sign out</button>
</header>
<div id="frame"></div>
<p id="empty" hidden>No dashboards are shared with you yet.</p>
<script type="module">
  import { mountDashboard, HostQueryError } from "/runtime/host.js";
  const me = __ME__;
  const $ = (id) => document.getElementById(id);
  $("me").textContent = me.name;
  const headers = { "content-type": "application/json", "x-tabfree-request": "1" };
  $("logout").onclick = async () => {
    await fetch("/auth/logout", { method: "POST", headers });
    location.href = "/";
  };
  const runtime = await (await fetch("/runtime/frame.js")).text();
  const list = await (await fetch("/api/dashboards")).json();
  if (!list.length) $("empty").hidden = false;
  for (const d of list) $("dashboard").append(new Option(`${d.project} / ${d.title}`, d.id));
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  let host = null;
  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
    const json = await response.json();
    if (!response.ok) {
      const code = response.status === 404 ? "not_found" : "query_failed";
      throw new HostQueryError(code, json.detail ?? "failed");
    }
    return json;
  }
  async function open(id) {
    host?.destroy();
    if (!id) return;
    const { manifest, html } = await (await fetch(`/api/dashboards/${encodeURIComponent(id)}`)).json();
    host = mountDashboard({
      container: $("frame"), manifest, html, runtime, theme: { mode: dark ? "dark" : "light" },
      runQuery: ({ query, state, selections }) =>
        post(`/api/dashboards/${id}/query`, { query, state, selections }),
      runDomain: ({ filter, state }) => post(`/api/dashboards/${id}/domain`, { filter, state }),
    });
  }
  $("dashboard").onchange = () => open($("dashboard").value);
  await open(list[0]?.id);
</script>
</body>
</html>
"""
