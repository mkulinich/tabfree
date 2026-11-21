"""OAuth 2.1 for the MCP server (BUILD_PLAN P4.1), as the MCP authorization spec describes it.

- **Protected resource metadata** (RFC 9728) at ``/.well-known/oauth-protected-resource``: the MCP
  endpoint, its scopes and the authorization server (this service).
- **Authorization server metadata** (RFC 8414) at ``/.well-known/oauth-authorization-server``.
- **Dynamic client registration** (RFC 7591) at ``/oauth/register``: public clients only (no client
  secrets), with https redirect URIs or loopback ``http://127.0.0.1``/``localhost`` ones (any port,
  RFC 8252).
- **Authorization code with PKCE (S256 only)** at ``/oauth/authorize``. The person signs in to TabFree
  as usual and approves the client on a consent page; the code lives five minutes and works once
  (a replay revokes every token issued from it).
- **Tokens** at ``/oauth/token``: access tokens (``tfo_``, one hour) and rotating refresh tokens
  (``tfr_``, 30 days; a replayed refresh token revokes its family). ``/oauth/revoke`` revokes.
- Tokens are bound to this service (the ``resource`` parameter, RFC 8707) and carry the person's
  identity: groups, site role and attributes are read fresh on every call, so a role change or a
  disabled account takes effect at once.

Agents inside TabFree get **on-behalf-of** access tokens from :meth:`OAuthStore.mint_agent_token`:
short-lived, tied to the agent session (audited with it), and unable to confirm a publish.

Only SHA-256 hashes of codes and tokens are stored.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import Engine, delete, insert, select, update

from tabfree_api.auth import SessionInfo
from tabfree_api.db import schema as t

ACCESS_PREFIX = "tfo_"
REFRESH_PREFIX = "tfr_"
CODE_TTL = dt.timedelta(minutes=5)
MAX_CLIENTS = 1000
SCOPES = ("tabfree.read", "tabfree.author")
INTERNAL_CLIENT_NAME = "TabFree agent"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def base(settings: Any) -> str:
    return str(settings.base_url).rstrip("/")


def mcp_resource(settings: Any) -> str:
    return base(settings) + "/mcp"


def resource_metadata_url(settings: Any) -> str:
    return base(settings) + "/.well-known/oauth-protected-resource/mcp"


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error, self.description, self.status = error, description, status

    def response(self) -> JSONResponse:
        return JSONResponse(
            {"error": self.error, "error_description": self.description},
            status_code=self.status,
            headers={"Cache-Control": "no-store"},
        )


def redirect_allowed(uri: str) -> bool:
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


def _loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


def redirect_matches(registered: list[str], uri: str) -> bool:
    """Exact match, except that loopback redirect URIs may use any port (RFC 8252 §7.3)."""
    if uri in registered:
        return True
    if not _loopback(uri):
        return False
    asked = urlsplit(uri)
    for candidate in registered:
        known = urlsplit(candidate)
        if _loopback(candidate) and (known.hostname, known.path, known.query) == (
            asked.hostname,
            asked.path,
            asked.query,
        ):
            return True
    return False


def _scopes(scope: str | None) -> str:
    asked = (scope or " ".join(SCOPES)).split()
    unknown = [s for s in asked if s not in SCOPES]
    if unknown:
        raise OAuthError("invalid_scope", f"unknown scope {unknown[0]!r}")
    return " ".join(s for s in SCOPES if s in asked)


class OAuthStore:
    def __init__(
        self, engine: Engine, site_id: str, sessions: Any, *, access_ttl_s: int, refresh_days: int
    ) -> None:
        self.engine = engine
        self.site_id = site_id
        self.sessions = sessions  # DbSessionStore: resolves the user's current groups and role
        self.access_ttl = dt.timedelta(seconds=access_ttl_s)
        self.refresh_ttl = dt.timedelta(days=refresh_days)

    def _uid(self, conn: Any, username: str) -> str:
        uid = conn.execute(select(t.users.c.id).where(t.users.c.username == username)).scalar()
        if uid is None:
            raise OAuthError("access_denied", "unknown user")
        return str(uid)

    # ---- clients -------------------------------------------------------------------------------------

    def register(self, name: str, redirect_uris: list[str]) -> dict[str, Any]:
        if not redirect_uris or len(redirect_uris) > 10:
            raise OAuthError("invalid_redirect_uri", "give 1 to 10 redirect_uris")
        for uri in redirect_uris:
            if not isinstance(uri, str) or len(uri) > 2000 or not redirect_allowed(uri):
                raise OAuthError(
                    "invalid_redirect_uri", "redirect URIs must be https, or http on 127.0.0.1 or localhost"
                )
        client_id = "tfc_" + secrets.token_urlsafe(18)
        when = _now()
        with self.engine.begin() as conn:
            count = len(
                conn.execute(
                    select(t.oauth_clients.c.id).where(t.oauth_clients.c.site_id == self.site_id)
                ).all()
            )
            if count >= MAX_CLIENTS:
                raise OAuthError("temporarily_unavailable", "too many registered clients", 503)
            conn.execute(
                insert(t.oauth_clients).values(
                    id=client_id,
                    site_id=self.site_id,
                    name=name[:200] or "MCP client",
                    redirect_uris=list(redirect_uris),
                    created_at=when,
                )
            )
        return {
            "client_id": client_id,
            "client_id_issued_at": int(when.timestamp()),
            "client_name": name[:200] or "MCP client",
            "redirect_uris": list(redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    def client(self, client_id: str) -> Mapping[str, Any] | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(t.oauth_clients).where(
                        t.oauth_clients.c.id == client_id[:64], t.oauth_clients.c.site_id == self.site_id
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    # ---- codes and tokens --------------------------------------------------------------------------

    def issue_code(
        self, client_id: str, username: str, redirect_uri: str, challenge: str, scope: str, resource: str
    ) -> str:
        code = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.execute(
                delete(t.oauth_codes).where(t.oauth_codes.c.expires_at < _now() - dt.timedelta(days=1))
            )
            conn.execute(
                insert(t.oauth_codes).values(
                    code_sha256=_sha(code),
                    client_id=client_id,
                    user_id=self._uid(conn, username),
                    redirect_uri=redirect_uri,
                    code_challenge=challenge,
                    scope=scope,
                    resource=resource,
                    expires_at=_now() + CODE_TTL,
                )
            )
        return code

    def _tokens(
        self,
        conn: Any,
        *,
        client_id: str | None,
        user_id: str,
        scope: str,
        resource: str,
        family: str,
        agent_session: str | None = None,
        access_ttl: dt.timedelta | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        when = _now()
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        ttl = access_ttl or self.access_ttl
        rows = [
            {"token_sha256": _sha(access), "kind": "access", "expires_at": when + ttl},
        ]
        out: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(ttl.total_seconds()),
            "scope": scope,
        }
        if refresh:
            token = REFRESH_PREFIX + secrets.token_urlsafe(32)
            rows.append(
                {"token_sha256": _sha(token), "kind": "refresh", "expires_at": when + self.refresh_ttl}
            )
            out["refresh_token"] = token
        for row in rows:
            conn.execute(
                insert(t.oauth_tokens).values(
                    **row,
                    site_id=self.site_id,
                    client_id=client_id,
                    user_id=user_id,
                    scope=scope,
                    resource=resource,
                    agent_session=agent_session,
                    family=family,
                    created_at=when,
                )
            )
        return out

    def _revoke_family(self, family: str | None) -> None:
        if family:
            with self.engine.begin() as conn:
                conn.execute(
                    update(t.oauth_tokens).where(t.oauth_tokens.c.family == family).values(revoked_at=_now())
                )

    def exchange_code(
        self, code: str, client_id: str, redirect_uri: str, verifier: str, resource: str | None
    ) -> dict[str, Any]:
        c = t.oauth_codes
        with self.engine.begin() as conn:
            row = conn.execute(select(c).where(c.c.code_sha256 == _sha(code))).mappings().first()
        if row is None or row["client_id"] != client_id:
            raise OAuthError("invalid_grant", "unknown code")
        if row["used_at"] is not None:
            # A replayed code: revoke whatever it issued (RFC 6749 §4.1.2).
            self._revoke_family(row["family"])
            raise OAuthError("invalid_grant", "this code was already used")
        if _aware(row["expires_at"]) < _now():
            raise OAuthError("invalid_grant", "the code has expired")
        if row["redirect_uri"] != redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
        if resource is not None and resource != row["resource"]:
            raise OAuthError("invalid_target", "resource does not match the authorization request")
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(digest, row["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed")
        family = str(uuid.uuid4())
        with self.engine.begin() as conn:
            # Single use, even under a race: only the request that marks the code gets tokens.
            marked = conn.execute(
                update(c)
                .where(c.c.code_sha256 == row["code_sha256"], c.c.used_at.is_(None))
                .values(used_at=_now(), family=family)
            ).rowcount
            if not marked:
                raise OAuthError("invalid_grant", "this code was already used")
            return self._tokens(
                conn,
                client_id=client_id,
                user_id=row["user_id"],
                scope=row["scope"],
                resource=row["resource"],
                family=family,
            )

    def refresh(self, token: str, client_id: str, scope: str | None) -> dict[str, Any]:
        tt = t.oauth_tokens
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(tt).where(
                        tt.c.token_sha256 == _sha(token), tt.c.kind == "refresh", tt.c.site_id == self.site_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["client_id"] != client_id:
            raise OAuthError("invalid_grant", "unknown refresh token")
        if row["revoked_at"] is not None:
            # Rotation: a used refresh token coming back means it leaked. End the family.
            self._revoke_family(row["family"])
            raise OAuthError("invalid_grant", "this refresh token was already used")
        if _aware(row["expires_at"]) < _now():
            raise OAuthError("invalid_grant", "the refresh token has expired")
        granted = row["scope"].split()
        narrowed = _scopes(scope) if scope else row["scope"]
        if any(s not in granted for s in narrowed.split()):
            raise OAuthError("invalid_scope", "a refresh cannot widen the scope")
        with self.engine.begin() as conn:
            rotated = conn.execute(
                update(tt)
                .where(tt.c.token_sha256 == row["token_sha256"], tt.c.revoked_at.is_(None))
                .values(revoked_at=_now())
            ).rowcount
            if not rotated:
                raise OAuthError("invalid_grant", "this refresh token was already used")
            return self._tokens(
                conn,
                client_id=client_id,
                user_id=row["user_id"],
                scope=narrowed,
                resource=row["resource"],
                family=row["family"],
            )

    def revoke(self, token: str) -> None:
        tt = t.oauth_tokens
        with self.engine.begin() as conn:
            row = conn.execute(select(tt.c.family, tt.c.kind).where(tt.c.token_sha256 == _sha(token))).first()
            if row is None:
                return
            if row[1] == "refresh":
                conn.execute(update(tt).where(tt.c.family == row[0]).values(revoked_at=_now()))
            else:
                conn.execute(update(tt).where(tt.c.token_sha256 == _sha(token)).values(revoked_at=_now()))

    def mint_agent_token(
        self,
        username: str,
        agent_session: str,
        resource: str,
        *,
        ttl_s: int = 900,
        scope: str = "tabfree.read tabfree.author",
    ) -> str:
        """An on-behalf-of access token for an agent session: no refresh, audited with the session id."""
        with self.engine.begin() as conn:
            out = self._tokens(
                conn,
                client_id=None,
                user_id=self._uid(conn, username),
                scope=_scopes(scope),
                resource=resource,
                family=str(uuid.uuid4()),
                agent_session=agent_session,
                access_ttl=dt.timedelta(seconds=ttl_s),
                refresh=False,
            )
        return str(out["access_token"])

    def revoke_agent_session(self, agent_session: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.oauth_tokens)
                .where(t.oauth_tokens.c.agent_session == agent_session)
                .values(revoked_at=_now())
            )

    def authenticate(self, token: str, resources: tuple[str, ...]) -> SessionInfo | None:
        tt = t.oauth_tokens
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(tt, t.users.c.username, t.users.c.fullname, t.users.c.disabled)
                    .join(t.users, t.users.c.id == tt.c.user_id)
                    .where(
                        tt.c.token_sha256 == _sha(token), tt.c.kind == "access", tt.c.site_id == self.site_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["revoked_at"] is not None or row["disabled"]:
            return None
        expires = _aware(row["expires_at"])
        if expires < _now() or row["resource"] not in resources:
            return None
        info: SessionInfo | None = self.sessions._pat_session(row["username"], row["fullname"], expires)
        if info is None:
            return None
        return replace(info, agent_session=row["agent_session"], scopes=frozenset(row["scope"].split()))


def resolve_bearer(ctx: Any, token: str) -> SessionInfo | None:
    """A bearer token: an OAuth access token, a personal access token or a session token."""
    if not token or len(token) > 300:
        return None
    if token.startswith(ACCESS_PREFIX):
        store: OAuthStore | None = getattr(ctx, "oauth", None)
        if store is None:
            return None
        return store.authenticate(token, (mcp_resource(ctx.settings), base(ctx.settings)))
    info: SessionInfo | None = ctx.sessions.get(token)
    return info


# ---- routes --------------------------------------------------------------------------------------------


def create_router(ctx: Any, store: OAuthStore) -> APIRouter:
    router = APIRouter()
    settings = ctx.settings
    key = (
        settings.oauth_signing_key.get_secret_value().encode()
        if settings.oauth_signing_key is not None
        else secrets.token_bytes(32)
    )

    def resource_metadata() -> dict[str, Any]:
        return {
            "resource": mcp_resource(settings),
            "authorization_servers": [base(settings)],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "TabFree MCP server",
        }

    @router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def protected_resource() -> dict[str, Any]:
        return resource_metadata()

    @router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    def authorization_server() -> dict[str, Any]:
        root = base(settings)
        return {
            "issuer": root,
            "authorization_endpoint": f"{root}/oauth/authorize",
            "token_endpoint": f"{root}/oauth/token",
            "registration_endpoint": f"{root}/oauth/register",
            "revocation_endpoint": f"{root}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": list(SCOPES),
            "authorization_response_iss_parameter_supported": True,
        }

    @router.post("/oauth/register", include_in_schema=False)
    async def register(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return OAuthError("invalid_client_metadata", "send JSON").response()
        if not isinstance(body, dict):
            return OAuthError("invalid_client_metadata", "send a JSON object").response()
        method = body.get("token_endpoint_auth_method", "none")
        if method != "none":
            return OAuthError(
                "invalid_client_metadata", "only public clients (token_endpoint_auth_method none)"
            ).response()
        try:
            client = store.register(
                str(body.get("client_name") or "MCP client"), list(body.get("redirect_uris") or [])
            )
        except OAuthError as exc:
            return exc.response()
        ctx.audit("oauth_register", None, client=client["client_id"], name=client["client_name"][:100])
        return JSONResponse(client, status_code=201, headers={"Cache-Control": "no-store"})

    def consent_sig(session_token: str, values: Mapping[str, str], expires: int) -> str:
        payload = "\n".join(
            [_sha(session_token), str(expires), *(f"{k}={values[k]}" for k in sorted(values))]
        )
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

    def redirect_error(redirect_uri: str, state: str, error: str) -> Response:
        query = urlencode({"error": error, "state": state, "iss": base(settings)})
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{query}", status_code=303)

    def check_request(values: Mapping[str, str]) -> tuple[Mapping[str, Any], str]:
        """The client, and the scope, of a valid authorization request; raises before any redirect."""
        client = store.client(values.get("client_id", ""))
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id")
        if not redirect_matches(list(client["redirect_uris"]), values.get("redirect_uri", "")):
            raise OAuthError("invalid_request", "redirect_uri is not registered for this client")
        return client, _scopes(values.get("scope"))

    @router.get("/oauth/authorize", include_in_schema=False)
    def authorize(request: Request, tabfree_session: Annotated[str | None, Cookie()] = None) -> Response:
        values = {k: str(request.query_params.get(k, ""))[:2000] for k in _PARAMS}
        try:
            client, scope = check_request(values)
        except OAuthError as exc:
            # Never redirect to an unverified redirect URI: show the error here.
            return HTMLResponse(
                _page("Sign-in request refused", f"<p>{html.escape(exc.description)}</p>"), status_code=400
            )
        redirect_uri, state = values["redirect_uri"], values["state"]
        if values["response_type"] != "code":
            return redirect_error(redirect_uri, state, "unsupported_response_type")
        if values["code_challenge_method"] != "S256" or not 43 <= len(values["code_challenge"]) <= 128:
            return redirect_error(redirect_uri, state, "invalid_request")
        if values["resource"] and values["resource"] not in (mcp_resource(settings), base(settings)):
            return redirect_error(redirect_uri, state, "invalid_target")
        info = ctx.sessions.get(tabfree_session) if tabfree_session else None
        if info is None:
            return RedirectResponse(
                f"/auth/login?{urlencode({'return': request.url.path + '?' + request.url.query})}",
                status_code=303,
            )
        expires = int(time.time()) + 600
        values["scope"] = scope
        sig = consent_sig(tabfree_session or "", values, expires)
        hidden = (
            "".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in values.items())
            + f'<input type="hidden" name="expires" value="{expires}">'
            + f'<input type="hidden" name="sig" value="{sig}">'
        )
        can = "<li>see the dashboards and models you can see, and run governed queries as you</li>" + (
            "<li>edit dashboards in private drafts and ask to publish them</li>"
            if "tabfree.author" in scope
            else ""
        )
        body = (
            f"<p><strong>{html.escape(str(client['name']))}</strong> wants to use TabFree as "
            f"<strong>{html.escape(info.fullname)}</strong>.</p><p>It will be able to:</p><ul>{can}</ul>"
            "<p>It gets exactly your permissions and row-level security, and it cannot publish anything "
            "without your confirmation.</p>"
            f"<p class=small>It will return to {html.escape(urlsplit(redirect_uri).netloc)}.</p>"
            f'<form method="post" action="/oauth/authorize">{hidden}'
            '<button name="decision" value="allow">Allow</button> '
            '<button name="decision" value="deny">Deny</button></form>'
        )
        # form-action also governs the redirect after the POST, so it names the client's origin.
        target = urlsplit(redirect_uri)
        headers = {
            **_PAGE_HEADERS,
            "Content-Security-Policy": _PAGE_HEADERS["Content-Security-Policy"]
            + f"; form-action 'self' {target.scheme}://{target.netloc}",
        }
        return HTMLResponse(_page("Allow access to TabFree?", body), headers=headers)

    @router.post("/oauth/authorize", include_in_schema=False)
    async def approve(request: Request, tabfree_session: Annotated[str | None, Cookie()] = None) -> Response:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != base(settings):
            return HTMLResponse(_page("Refused", "<p>Cross-site request.</p>"), status_code=403)
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), max_num_fields=20).items()}
        values = {k: form.get(k, "")[:2000] for k in _PARAMS}
        info = ctx.sessions.get(tabfree_session) if tabfree_session else None
        try:
            expires = int(form.get("expires", "0"))
        except ValueError:
            expires = 0
        if (
            info is None
            or tabfree_session is None
            or expires < time.time()
            or not hmac.compare_digest(form.get("sig", ""), consent_sig(tabfree_session, values, expires))
        ):
            return HTMLResponse(
                _page("Expired", "<p>This request expired. Start again from your app.</p>"), status_code=400
            )
        try:
            check_request(values)
        except OAuthError as exc:
            return HTMLResponse(_page("Refused", f"<p>{html.escape(exc.description)}</p>"), status_code=400)
        redirect_uri, state = values["redirect_uri"], values["state"]
        if form.get("decision") != "allow":
            ctx.audit("oauth_consent", info.username, "deny", client=values["client_id"])
            return redirect_error(redirect_uri, state, "access_denied")
        code = store.issue_code(
            values["client_id"],
            info.username,
            redirect_uri,
            values["code_challenge"],
            values["scope"],
            values["resource"] or mcp_resource(settings),
        )
        ctx.audit("oauth_consent", info.username, client=values["client_id"], scope=values["scope"])
        sep = "&" if "?" in redirect_uri else "?"
        query = urlencode({"code": code, "state": state, "iss": base(settings)})
        return RedirectResponse(f"{redirect_uri}{sep}{query}", status_code=303)

    @router.post("/oauth/token", include_in_schema=False)
    async def token(request: Request) -> Response:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), max_num_fields=20).items()}
        grant = form.get("grant_type")
        try:
            if grant == "authorization_code":
                out = store.exchange_code(
                    form.get("code", ""),
                    form.get("client_id", ""),
                    form.get("redirect_uri", ""),
                    form.get("code_verifier", ""),
                    form.get("resource"),
                )
            elif grant == "refresh_token":
                out = store.refresh(
                    form.get("refresh_token", ""), form.get("client_id", ""), form.get("scope")
                )
            else:
                raise OAuthError("unsupported_grant_type", "use authorization_code or refresh_token")
        except OAuthError as exc:
            return exc.response()
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    @router.post("/oauth/revoke", include_in_schema=False)
    async def revoke(request: Request) -> Response:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), max_num_fields=5).items()}
        store.revoke(form.get("token", ""))
        return Response(status_code=200)

    return router


_PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "scope",
    "resource",
)

_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def _page(title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>:root{color-scheme:light dark;font:15px/1.5 system-ui,sans-serif}"
        "body{max-width:520px;margin:48px auto;padding:0 16px}button{font:inherit;padding:6px 14px}"
        ".small{font-size:13px;opacity:.75}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )
