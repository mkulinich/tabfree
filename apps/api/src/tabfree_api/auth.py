"""Sign-in with OpenID Connect, and server-side sessions (BUILD_PLAN P1.13; P3.2 extends it).

Flow: authorization code with PKCE, plus state and nonce.

1. ``/auth/login`` stores a pending flow (state, nonce, PKCE verifier) server-side, sets the state in a
   short-lived cookie that binds the flow to this browser, and redirects to the provider.
2. ``/auth/callback`` checks the state against both, exchanges the code at the token endpoint,
   validates the ID token (signature from the provider's JWKS, issuer, audience, expiry, nonce),
   and starts a session.

Sessions are random 256-bit tokens in an HttpOnly cookie; only their SHA-256 is stored. A dev-only
login (``TABFREE_DEV_LOGIN=1`` in a dev environment) signs in as a user from the site file, for
local work without an identity provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import jwt

from tabfree_api.authz import Principal, SiteRole

SESSION_COOKIE = "tabfree_session"
FLOW_COOKIE = "tabfree_oidc_state"
FLOW_TTL_S = 600
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class AuthError(Exception):
    """Sign-in failed. The message is safe to show."""


# ---- HTTP (injectable so tests run without a network) ----------------------------------------


class Http(Protocol):
    def get_json(self, url: str) -> Mapping[str, Any]: ...

    def post_form(
        self, url: str, data: Mapping[str, str], auth: tuple[str, str] | None
    ) -> Mapping[str, Any]: ...


class UrllibHttp:
    timeout_s = 10.0

    def get_json(self, url: str) -> Mapping[str, Any]:
        _require_https(url)
        with urllib.request.urlopen(url, timeout=self.timeout_s) as response:  # noqa: S310 - https checked
            return _json_object(response.read())

    def post_form(self, url: str, data: Mapping[str, str], auth: tuple[str, str] | None) -> Mapping[str, Any]:
        _require_https(url)
        request = urllib.request.Request(  # noqa: S310 - https checked
            url, data=urllib.parse.urlencode(data).encode(), method="POST"
        )
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Accept", "application/json")
        if auth:
            token = base64.b64encode(f"{_q(auth[0])}:{_q(auth[1])}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
                return _json_object(response.read())
        except urllib.error.HTTPError as exc:
            raise AuthError(f"the identity provider refused the sign-in ({exc.code})") from None


def _q(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _json_object(raw: bytes) -> Mapping[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise AuthError("unexpected response from the identity provider")
    return value


def _require_https(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise AuthError("identity provider URLs must use https")


# ---- Sessions ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionInfo:
    username: str
    fullname: str
    groups: frozenset[str]
    site_role: SiteRole
    expires: float
    email: str = ""
    #: User attributes for RLS (``USERATTRIBUTE``): identity-provider claims and admin-set values.
    attributes: Mapping[str, str] = field(default_factory=dict)
    #: Set when an agent acts for the user (an on-behalf-of token, P4.3/P4.4): audited, and the agent
    #: may not confirm a publish.
    agent_session: str | None = None
    #: OAuth scopes of the token (``tabfree.read``, ``tabfree.author``); None means a full session.
    scopes: frozenset[str] | None = None

    def principal(self) -> Principal:
        return Principal(self.username, self.groups, self.site_role, self.fullname, self.attributes)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    """Sessions and pending sign-ins in SQLite (Postgres in Phase 3)."""

    def __init__(self, path: Path | str) -> None:
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._con:
            self._con.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  token_sha256 TEXT PRIMARY KEY, username TEXT NOT NULL, fullname TEXT NOT NULL,
                  groups_json TEXT NOT NULL, site_role TEXT NOT NULL, created REAL NOT NULL,
                  expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS oidc_flows (
                  state_sha256 TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL,
                  return_to TEXT NOT NULL, expires REAL NOT NULL);
                """
            )

    def create(
        self,
        username: str,
        fullname: str,
        groups: frozenset[str],
        role: SiteRole,
        ttl_s: float,
        *,
        email: str = "",
        attributes: Mapping[str, str] | None = None,
    ) -> str:
        """A session. The SQLite store (Phase 1 mode) does not keep email or attributes."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._con:
            self._con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            self._con.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _digest(token),
                    username,
                    fullname,
                    json.dumps(sorted(groups)),
                    role.value,
                    now,
                    now + ttl_s,
                ),
            )
        return token

    def get(self, token: str | None) -> SessionInfo | None:
        if not token or len(token) > 200:
            return None
        with self._lock:
            row = self._con.execute(
                "SELECT username, fullname, groups_json, site_role, expires FROM sessions "
                "WHERE token_sha256 = ?",
                (_digest(token),),
            ).fetchone()
        if row is None or row[4] < time.time():
            return None
        return SessionInfo(row[0], row[1], frozenset(json.loads(row[2])), SiteRole(row[3]), row[4])

    def delete(self, token: str | None) -> None:
        if token:
            with self._lock, self._con:
                self._con.execute("DELETE FROM sessions WHERE token_sha256 = ?", (_digest(token),))

    def start_flow(self, return_to: str) -> tuple[str, str, str]:
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        now = time.time()
        with self._lock, self._con:
            self._con.execute("DELETE FROM oidc_flows WHERE expires < ?", (now,))
            self._con.execute(
                "INSERT INTO oidc_flows VALUES (?, ?, ?, ?, ?)",
                (_digest(state), nonce, verifier, return_to, now + FLOW_TTL_S),
            )
        return state, nonce, verifier

    def finish_flow(self, state: str) -> tuple[str, str, str] | None:
        """Consume a pending flow (single use). Returns (nonce, verifier, return_to)."""
        with self._lock, self._con:
            row = self._con.execute(
                "SELECT nonce, verifier, return_to, expires FROM oidc_flows WHERE state_sha256 = ?",
                (_digest(state),),
            ).fetchone()
            self._con.execute("DELETE FROM oidc_flows WHERE state_sha256 = ?", (_digest(state),))
        if row is None or row[3] < time.time():
            return None
        return row[0], row[1], row[2]


# ---- OIDC client ------------------------------------------------------------------------------


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass(frozen=True)
class Identity:
    username: str
    fullname: str
    email: str | None
    groups: frozenset[str]
    attributes: Mapping[str, str] = field(default_factory=dict)


class OidcClient:
    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str | None,
        redirect_uri: str,
        scopes: str = "openid email profile",
        username_claim: str = "preferred_username",
        groups_claim: str = "groups",
        attribute_claims: tuple[str, ...] = (),
        http: Http | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.username_claim = username_claim
        self.groups_claim = groups_claim
        self.attribute_claims = attribute_claims
        self._http = http or UrllibHttp()
        self._discovery: Mapping[str, Any] | None = None
        self._jwks: jwt.PyJWKSet | None = None

    def discovery(self) -> Mapping[str, Any]:
        if self._discovery is None:
            doc = self._http.get_json(f"{self.issuer}/.well-known/openid-configuration")
            if str(doc.get("issuer", "")).rstrip("/") != self.issuer:
                raise AuthError("the identity provider's discovery document names a different issuer")
            self._discovery = doc
        return self._discovery

    def authorize_url(self, state: str, nonce: str, verifier: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        endpoint = str(self.discovery()["authorization_endpoint"])
        _require_https(endpoint)
        return f"{endpoint}?{urllib.parse.urlencode(params)}"

    def exchange(self, code: str, nonce: str, verifier: str) -> Identity:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
            "client_id": self.client_id,
        }
        auth = (self.client_id, self._secret) if self._secret else None
        tokens = self._http.post_form(str(self.discovery()["token_endpoint"]), data, auth)
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise AuthError("the identity provider returned no ID token")
        return self.validate(id_token, nonce)

    def _key(self, token: str) -> Any:
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in _ALGORITHMS:
            raise AuthError("the ID token uses an unsupported algorithm")
        kid = header.get("kid")
        for attempt in range(2):
            if self._jwks is None or attempt:
                self._jwks = jwt.PyJWKSet.from_dict(
                    dict(self._http.get_json(str(self.discovery()["jwks_uri"])))
                )
            for key in self._jwks.keys:
                if kid is None or key.key_id == kid:
                    return key.key
        raise AuthError("the ID token's signing key is unknown")

    def validate(self, id_token: str, nonce: str) -> Identity:
        try:
            claims = jwt.decode(
                id_token,
                self._key(id_token),
                algorithms=_ALGORITHMS,
                audience=self.client_id,
                issuer=str(self.discovery()["issuer"]),
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"the ID token is not valid ({type(exc).__name__})") from None
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise AuthError("the ID token's nonce does not match this sign-in")
        username = claims.get(self.username_claim) or claims.get("email") or claims["sub"]
        groups = claims.get(self.groups_claim) or []
        if not isinstance(groups, list):
            groups = [groups]
        return Identity(
            username=str(username),
            fullname=str(claims.get("name") or username),
            email=str(claims["email"]) if claims.get("email") else None,
            groups=frozenset(str(g) for g in groups),
            attributes={
                claim: ",".join(map(str, value)) if isinstance(value, list) else str(value)
                for claim in self.attribute_claims
                if (value := claims.get(claim)) is not None
            },
        )


def safe_return_to(value: str | None) -> str:
    """Only same-site relative paths, so the login cannot redirect to another site."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value
