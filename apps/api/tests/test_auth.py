"""OIDC sign-in end to end against a fake identity provider (real keys and JWTs, no network)."""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tabfree_api.auth import AuthError, OidcClient, SessionStore, pkce_challenge, safe_return_to
from tabfree_api.authz import SiteRole
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

ISSUER = "https://idp.example.test"
CLIENT_ID = "tabfree"


class FakeIdP:
    """Discovery, JWKS and token endpoints; remembers the PKCE challenge of the last authorize call."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict[str, Any] = {}
        self.challenge: str | None = None
        self.nonce: str | None = None
        self.sign_with = self.key
        self.token_requests: list[Mapping[str, str]] = []

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": "k1", "use": "sig", "alg": "RS256"}]}

    def get_json(self, url: str) -> Mapping[str, Any]:
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            }
        if url == f"{ISSUER}/jwks":
            return self.jwks()
        raise AssertionError(url)

    def post_form(self, url: str, data: Mapping[str, str], auth: tuple[str, str] | None) -> Mapping[str, Any]:
        assert url == f"{ISSUER}/token"
        assert auth == (CLIENT_ID, "s3cret")
        self.token_requests.append(data)
        if data["code"] != "good-code" or pkce_challenge(data["code_verifier"]) != self.challenge:
            raise AuthError("the identity provider refused the sign-in (400)")
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "u-1",
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
        }
        token = jwt.encode(
            {**claims, **self.claims}, self.sign_with, algorithm="RS256", headers={"kid": "k1"}
        )
        return {"id_token": token, "access_token": "unused", "token_type": "Bearer"}


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def client(idp: FakeIdP, tmp_path: Path) -> TestClient:
    settings = Settings(
        environment="test",
        base_url="https://tabfree.example.test",
        session_db=tmp_path / "s.db",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="s3cret",  # type: ignore[arg-type]
    )
    app = create_app(settings)
    oidc = app.state.context.oidc
    assert isinstance(oidc, OidcClient)
    oidc._http = idp  # no network: the fake provider answers
    return TestClient(app, base_url="https://tabfree.example.test")


def start_login(client: TestClient, idp: FakeIdP, return_to: str = "/") -> str:
    response = client.get("/auth/login", params={"return": return_to}, follow_redirects=False)
    assert response.status_code == 303
    url = urllib.parse.urlparse(response.headers["location"])
    params = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "idp.example.test"
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "https://tabfree.example.test/auth/callback"
    idp.challenge, idp.nonce = params["code_challenge"], params["nonce"]
    return params["state"]


def test_full_sign_in_creates_a_session_with_groups(client: TestClient, idp: FakeIdP) -> None:
    idp.claims = {"preferred_username": "erin", "name": "Erin E", "groups": ["Executives"]}
    state = start_login(client, idp, "/?dashboard=sales-overview")
    response = client.get(
        "/auth/callback", params={"state": state, "code": "good-code"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?dashboard=sales-overview"
    cookie = response.headers["set-cookie"]
    assert "tabfree_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    me = client.get("/api/me").json()
    assert me == {"username": "erin", "fullname": "Erin E", "groups": ["Executives"], "siteRole": "explorer"}
    listed = [d["id"] for d in client.get("/api/dashboards").json()]
    assert listed == ["dimensional-hierarchy", "sales-analysis", "sales-overview"]


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("wrong_key", "not valid"),
        ("wrong_audience", "not valid"),
        ("expired", "not valid"),
        ("wrong_nonce", "nonce"),
    ],
)
def test_invalid_id_tokens_are_refused(client: TestClient, idp: FakeIdP, tamper: str, message: str) -> None:
    idp.claims = {"preferred_username": "erin"}
    if tamper == "wrong_key":
        idp.sign_with = idp.other_key
    elif tamper == "wrong_audience":
        idp.claims["aud"] = "someone-else"
    elif tamper == "expired":
        idp.claims["exp"] = int(time.time()) - 3600
    state = start_login(client, idp)
    if tamper == "wrong_nonce":
        idp.nonce = "not-the-nonce"
    response = client.get(
        "/auth/callback", params={"state": state, "code": "good-code"}, follow_redirects=False
    )
    assert response.status_code == 401
    assert message in response.json()["detail"]
    assert client.get("/api/me").status_code == 401


def test_state_must_match_this_browser_and_is_single_use(client: TestClient, idp: FakeIdP) -> None:
    idp.claims = {"preferred_username": "erin"}
    state = start_login(client, idp)
    forged = client.get(
        "/auth/callback", params={"state": "attacker", "code": "good-code"}, follow_redirects=False
    )
    assert forged.status_code == 400
    ok = client.get("/auth/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
    assert ok.status_code == 303
    client.cookies.set("tabfree_oidc_state", state, domain="tabfree.example.test", path="/auth")
    replay = client.get(
        "/auth/callback", params={"state": state, "code": "good-code"}, follow_redirects=False
    )
    assert replay.status_code == 400  # the flow was consumed


def test_bad_code_and_provider_errors(client: TestClient, idp: FakeIdP) -> None:
    state = start_login(client, idp)
    bad = client.get("/auth/callback", params={"state": state, "code": "stolen"}, follow_redirects=False)
    assert bad.status_code == 401
    denied = client.get("/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert denied.status_code == 401


def test_logout_needs_the_csrf_header_and_ends_the_session(client: TestClient, idp: FakeIdP) -> None:
    idp.claims = {"preferred_username": "alice", "groups": ["Managers"]}
    state = start_login(client, idp)
    client.get("/auth/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
    assert client.post("/auth/logout").status_code == 403
    assert client.post("/auth/logout", headers={"X-TabFree-Request": "1"}).status_code == 204
    assert client.get("/api/me").status_code == 401


def test_return_paths_cannot_leave_the_site() -> None:
    assert safe_return_to("/dashboards/x?y=1") == "/dashboards/x?y=1"
    for bad in ("https://evil.test", "//evil.test", "/\\evil.test", "javascript:alert(1)", None, ""):
        assert safe_return_to(bad) == "/"


def test_insecure_or_mismatched_providers_are_refused(idp: FakeIdP) -> None:
    with pytest.raises(AuthError, match="https"):
        OidcClient(
            issuer="http://idp.example.test", client_id="x", client_secret=None, redirect_uri="x"
        ).discovery()

    class Liar(FakeIdP):
        def get_json(self, url: str) -> Mapping[str, Any]:
            return {**super().get_json(url), "issuer": "https://evil.test"}

    liar = OidcClient(issuer=ISSUER, client_id="x", client_secret=None, redirect_uri="x", http=Liar())
    with pytest.raises(AuthError, match="different issuer"):
        liar.discovery()


def test_sessions_store_only_hashes_and_expire(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    token = store.create("alice", "Alice", frozenset({"Managers"}), SiteRole.VIEWER, ttl_s=60)
    assert store.get(token) is not None
    assert token not in (tmp_path / "s.db").read_bytes().decode("latin-1")
    expired = store.create("bob", "Bob", frozenset(), SiteRole.VIEWER, ttl_s=-1)
    assert store.get(expired) is None
    store.delete(token)
    assert store.get(token) is None
    assert store.get("x" * 500) is None
