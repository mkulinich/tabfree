"""Sign-in routes, the governed dashboard API and a minimal portal page (P1.13).

No ``from __future__ import annotations`` here: FastAPI must evaluate the ``Depends`` annotations,
which name functions local to :func:`create_router`.

Every API call needs a session. Dashboards are listed and served only when the viewer has the View
capability; queries run through :class:`QueryService` with the viewer's identity, so row-level
security comes from the semantic model. Ad-hoc semantic queries (``/api/models/{model}/query``, P2.7)
need the Connect capability on the model, which the site file grants (default deny).
State-changing requests must carry the ``X-TabFree-Request`` header, which a cross-site form or
image cannot send (CSRF defence).
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
from tabfree_api.content import ContentError, SiteContent
from tabfree_api.engines import EngineError, EngineTimeoutError
from tabfree_api.service import QueryService
from tabfree_api.settings import Settings
from tabfree_compiler import CompileError, Session

log = logging.getLogger("tabfree.api")


class Context:
    def __init__(
        self,
        settings: Settings,
        site: SiteContent,
        sessions: SessionStore | Any,
        service: QueryService,
        oidc: OidcClient | None,
        today: dt.date | None = None,
        *,
        accounts: Any = None,
        audit: Any = None,
    ) -> None:
        self.settings, self.site, self.sessions, self.service, self.oidc = (
            settings,
            site,
            sessions,
            service,
            oidc,
        )
        self.today = today
        #: Local accounts (``db.store.LocalAccounts``) and the audit log; None without a database.
        self.accounts = accounts
        self.audit_log = audit

    def audit(self, action: str, actor: str | None, outcome: str = "ok", **detail: Any) -> None:
        """An audit event: in the database when there is one, always in the service log."""
        log.info("audit %s actor=%s outcome=%s %s", action, actor, outcome, detail)
        if self.audit_log is not None:
            self.audit_log.record(action, actor=actor, outcome=outcome, **detail)


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    days: int = Field(default=365, ge=1, le=365)


class QueryBody(BaseModel):
    query: str = Field(max_length=64)
    state: dict[str, Any]
    selections: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class DomainBody(BaseModel):
    filter: str = Field(max_length=64)
    state: dict[str, Any]


class ExploreBody(BaseModel):
    #: A manifest-style query: dimensions, measures, where, sort, limit (schema ``$defs.query``).
    query: dict[str, Any]


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

    def bearer(authorization: str | None) -> str | None:
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return None

    def current(
        tabfree_session: Annotated[str | None, Cookie()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionInfo:
        # Scripts and the MCP server send a personal access token; browsers send the session cookie.
        token = bearer(authorization) or tabfree_session
        info = ctx.sessions.get(token)
        if info is None:
            raise HTTPException(401, "sign in first")
        return info

    def csrf(
        x_tabfree_request: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        # A bearer token is never attached by the browser on its own, so it needs no CSRF header.
        if x_tabfree_request != "1" and bearer(authorization) is None:
            raise HTTPException(403, "missing the X-TabFree-Request header")

    def viewer_session(info: SessionInfo) -> Session:
        return Session(username=info.username, fullname=info.fullname, groups=info.groups, today=ctx.today)

    def require_view(info: SessionInfo, dashboard: str) -> None:
        # 404, not 403, so viewers cannot probe which dashboards exist.
        if not ctx.site.can(info.principal(), Capability.VIEW, dashboard):
            ctx.audit("view", info.username, "deny", resource_kind="dashboard", resource_id=dashboard[:100])
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
        fullname, groups, role = ctx.site.resolve(identity.username, identity.fullname, identity.groups)
        if role is SiteRole.UNLICENSED:
            ctx.audit("sign_in", identity.username, "deny", method="oidc", reason="unlicensed")
            raise HTTPException(403, "your account has no license on this site")
        token = ctx.sessions.create(identity.username, fullname, groups, role, settings.session_hours * 3600)
        ctx.audit("sign_in", identity.username, method="oidc")
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
        fullname, groups, role = ctx.site.resolve(site_user.username, site_user.fullname, frozenset())
        token = ctx.sessions.create(site_user.username, fullname, groups, role, settings.session_hours * 3600)
        ctx.audit("sign_in", site_user.username, method="dev")
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        return response

    @router.get("/auth/local", response_class=HTMLResponse, include_in_schema=False)
    def local_page(error: str | None = None) -> HTMLResponse:
        if ctx.accounts is None:
            raise HTTPException(404)
        note = '<p role="alert">Wrong username or password.</p>' if error else ""
        form = (
            f'{note}<form method="post" action="/auth/local">'
            '<p><label>Username <input name="username" autocomplete="username" required></label></p>'
            '<p><label>Password <input name="password" type="password" autocomplete="current-password" '
            "required></label></p><p><button>Sign in</button></p></form>"
        )
        return HTMLResponse(_shell("Sign in", form))

    @router.post("/auth/local", include_in_schema=False)
    async def local_login(request: Request) -> Response:
        """Password sign-in for local accounts (break-glass administrators, service users)."""
        if ctx.accounts is None:
            raise HTTPException(404)
        from urllib.parse import parse_qs

        form = parse_qs((await request.body()).decode(), max_num_fields=5)
        username = (form.get("username") or [""])[0][:255]
        password = (form.get("password") or [""])[0][:1024]
        site_user = ctx.site.users.get(username)
        if site_user is None or not ctx.accounts.verify(username, password):
            ctx.audit("sign_in", username or None, "deny", method="local")
            return RedirectResponse("/auth/local?error=1", status_code=303)
        fullname, groups, role = ctx.site.resolve(username, site_user.fullname, frozenset())
        if role is SiteRole.UNLICENSED:
            ctx.audit("sign_in", username, "deny", method="local", reason="unlicensed")
            raise HTTPException(403, "your account has no license on this site")
        token = ctx.sessions.create(username, fullname, groups, role, settings.session_hours * 3600)
        ctx.audit("sign_in", username, method="local")
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, token)
        return response

    @router.post("/auth/logout", include_in_schema=False)
    def logout(
        _: Annotated[None, Depends(csrf)], tabfree_session: Annotated[str | None, Cookie()] = None
    ) -> Response:
        info = ctx.sessions.get(tabfree_session)
        ctx.sessions.delete(tabfree_session)
        if info is not None:
            ctx.audit("sign_out", info.username)
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

    # ---- personal access tokens (P3.2) -------------------------------------------------------------

    def pat_store() -> Any:
        if not hasattr(ctx.sessions, "create_pat"):
            raise HTTPException(404, "personal access tokens need the database (TABFREE_DATABASE_URL)")
        return ctx.sessions

    @router.get("/api/tokens")
    def list_tokens(info: Annotated[SessionInfo, Depends(current)]) -> list[dict[str, Any]]:
        return list(pat_store().list_pats(info.username))

    @router.post("/api/tokens", status_code=201)
    def create_token(
        body: TokenBody,
        info: Annotated[SessionInfo, Depends(current)],
        _: Annotated[None, Depends(csrf)],
    ) -> dict[str, str]:
        """A personal access token for scripts and the MCP server; the secret is shown only here."""
        try:
            token = pat_store().create_pat(info.username, body.name, days=body.days)
        except ContentError as exc:
            raise HTTPException(409, str(exc)) from None
        ctx.audit("token_create", info.username, name=body.name)
        return {"name": body.name, "token": token}

    @router.delete("/api/tokens/{token_id}", status_code=204)
    def revoke_token(
        token_id: str,
        info: Annotated[SessionInfo, Depends(current)],
        _: Annotated[None, Depends(csrf)],
    ) -> Response:
        if not pat_store().revoke_pat(info.username, token_id[:36]):
            raise HTTPException(404, "no such token")
        ctx.audit("token_revoke", info.username, token=token_id[:36])
        return Response(status_code=204)

    @router.get("/api/dashboards")
    def dashboards(info: Annotated[SessionInfo, Depends(current)]) -> list[dict[str, str]]:
        return ctx.site.visible(info.principal())

    @router.get("/api/dashboards/{dashboard}")
    def bundle(dashboard: str, info: Annotated[SessionInfo, Depends(current)]) -> dict[str, Any]:
        require_view(info, dashboard)
        b = ctx.site.bundle(dashboard)
        ctx.audit("view", info.username, resource_kind="dashboard", resource_id=dashboard)
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
        except EngineTimeoutError as exc:
            raise HTTPException(504, str(exc)) from None
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
        except EngineTimeoutError as exc:
            raise HTTPException(504, str(exc)) from None
        except EngineError as exc:
            raise HTTPException(502, str(exc)) from None

    @router.post("/api/models/{model}/query")
    def explore(
        model: str,
        body: ExploreBody,
        info: Annotated[SessionInfo, Depends(current)],
        _: Annotated[None, Depends(csrf)],
    ) -> dict[str, Any]:
        """Ad-hoc semantic query against a model the viewer may Connect to; RLS still applies."""
        # 404, not 403, so viewers cannot probe which models exist.
        if not ctx.site.can_model(info.principal(), Capability.CONNECT, model):
            ctx.audit("explore", info.username, "deny", resource_kind="model", resource_id=model[:100])
            raise HTTPException(404, f"no model {model!r}")
        try:
            return ctx.service.explore(model, body.query, viewer_session(info))
        except (CompileError, ContentError) as exc:
            raise HTTPException(400, str(exc)) from None
        except EngineTimeoutError as exc:
            raise HTTPException(504, str(exc)) from None
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
