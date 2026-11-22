"""Phase 6: embedding (P6.8), limited tokens, and SCIM provisioning (P6.9)."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.auth import allowed_by_scopes
from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.embed import attributes, sign_token
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

CSRF = {"X-TabFree-Request": "1"}
SCIM = "application/scim+json"


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'embed.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    return create_app(
        Settings(
            environment="test",
            dev_login=True,
            database_url=url,
            site_id="dev",
            permission_cache_s=0,
            base_url="http://testserver",
        )
    )


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def test_scopes_limit_what_tokens_may_call() -> None:
    embed = frozenset({"tabfree.embed:sales-overview"})
    assert allowed_by_scopes(embed, "GET", "/api/dashboards/sales-overview")
    assert allowed_by_scopes(embed, "POST", "/api/dashboards/sales-overview/query")
    assert not allowed_by_scopes(embed, "POST", "/api/dashboards/other/query")
    assert not allowed_by_scopes(embed, "GET", "/api/content")
    assert not allowed_by_scopes(embed, "DELETE", "/api/content/dashboard/sales-overview")
    read = frozenset({"tabfree.read"})
    assert allowed_by_scopes(read, "GET", "/api/content")
    assert allowed_by_scopes(read, "POST", "/api/dashboards/x/query")
    assert not allowed_by_scopes(read, "POST", "/api/drafts")
    author = frozenset({"tabfree.author"})
    assert allowed_by_scopes(author, "POST", "/api/drafts")
    assert not allowed_by_scopes(author, "POST", "/api/tokens")
    assert not allowed_by_scopes(author, "GET", "/api/admin/users")


def make_app(app: Any, domains: list[str] | None = None, projects: list[str] | None = None) -> dict[str, Any]:
    body = {
        "name": "Intranet",
        "domains": domains or ["https://intranet.example"],
        "projects": projects or [],
    }
    response = as_user(app, "admin").post("/api/connected-apps", json=body, headers=CSRF)
    assert response.status_code == 201, response.text
    return dict(response.json())


def token(connected: dict[str, Any], sub: str = "erin", **extra: Any) -> str:
    claims = {
        "iss": connected["id"],
        "aud": "tabfree",
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "exp": int(time.time()) + 300,
        "scp": ["tabfree:views:embed"],
        **extra,
    }
    return sign_token(connected["secret"], {"kid": connected["secretId"], "iss": connected["id"]}, claims)


def test_embed_tokens_become_limited_single_use_sessions(app: Any) -> None:
    connected = make_app(app)
    assert as_user(app, "erin").get("/api/connected-apps").status_code == 403
    listed = as_user(app, "admin").get("/api/connected-apps").json()
    assert "secret" not in listed[0]
    anonymous = TestClient(app)
    good = token(connected, region="East")
    session = anonymous.post("/api/embed/session", json={"token": good, "dashboard": "sales-overview"})
    assert session.status_code == 200, session.text
    assert session.json()["origins"] == ["https://intranet.example"]
    # Single use: the same jti is refused.
    again = anonymous.post("/api/embed/session", json={"token": good, "dashboard": "sales-overview"})
    assert again.status_code == 401
    assert "already used" in again.json()["detail"]
    bearer = {"Authorization": f"Bearer {session.json()['session']}", **CSRF}
    assert anonymous.get("/api/dashboards/sales-overview", headers=bearer).status_code == 200
    assert anonymous.get("/api/dashboards/sales-analysis", headers=bearer).status_code == 403
    assert anonymous.get("/api/content", headers=bearer).status_code == 403
    assert anonymous.post("/api/tokens", json={"name": "x"}, headers=bearer).status_code == 403
    info = app.state.context.sessions.get(session.json()["session"])
    assert info.attributes["region"] == "East"
    assert info.scopes == frozenset({"tabfree.embed:sales-overview"})


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"exp": int(time.time()) + 3600}, "at most"),
        ({"exp": int(time.time()) - 5}, "expired"),
        ({"aud": "other"}, "audience"),
        ({"scp": ["tabfree:read"]}, "embed scope"),
        ({"sub": "nobody"}, "no such user"),
    ],
)
def test_bad_embed_tokens_are_refused(app: Any, change: dict[str, Any], reason: str) -> None:
    connected = make_app(app)
    refused = TestClient(app).post(
        "/api/embed/session", json={"token": token(connected, **change), "dashboard": "sales-overview"}
    )
    assert refused.status_code == 401
    assert reason in refused.json()["detail"]


def test_signatures_permissions_and_project_limits(app: Any) -> None:
    connected = make_app(app, projects=["migrated"])
    forged = token({**connected, "secret": "not-the-secret"})
    client = TestClient(app)
    assert (
        client.post("/api/embed/session", json={"token": forged, "dashboard": "sales-overview"}).status_code
        == 401
    )
    # Mallory may not view the dashboard; and this app may only embed from the "migrated" project.
    assert (
        client.post(
            "/api/embed/session",
            json={"token": token(connected, sub="mallory"), "dashboard": "sales-overview"},
        ).status_code
        == 404
    )
    limited = client.post(
        "/api/embed/session", json={"token": token(connected), "dashboard": "sales-overview"}
    )
    assert limited.status_code == 403
    # Rotating the secret invalidates tokens signed with the old one.
    rotated = as_user(app, "admin").post(f"/api/connected-apps/{connected['id']}/rotate", headers=CSRF).json()
    assert rotated["secretId"] != connected["secretId"]
    assert (
        client.post(
            "/api/embed/session", json={"token": token(connected), "dashboard": "dimensional-hierarchy"}
        ).status_code
        == 401
    )


def test_the_embed_page_is_framed_only_by_connected_apps(app: Any) -> None:
    make_app(app, domains=["https://intranet.example", "https://portal.example:8443"])
    page = TestClient(app).get("/embed/sales-overview")
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'self' https://intranet.example https://portal.example:8443" in csp
    assert "nonce-" in csp
    assert 'const dashboard = location.pathname.split("/").pop();' in page.text
    assert TestClient(app).get("/embed/..%2Fapi").status_code == 404
    assert TestClient(app).get("/embed/sales%0A").status_code == 404  # no trailing newline past the check
    script = TestClient(app).get("/embed.js")
    assert "window.TabFree" in script.text
    assert script.headers["content-type"].startswith("text/javascript")
    bad = as_user(app, "admin").post(
        "/api/connected-apps", json={"name": "x", "domains": ["http://evil.example"]}, headers=CSRF
    )
    assert bad.status_code == 422


def test_extra_claims_become_attributes() -> None:
    assert attributes(
        {"sub": "x", "region": "East", "level": 3, "flag": True, "bad key": "y", "nested": {"a": 1}}
    ) == {
        "region": "East",
        "level": "3",
    }


# -- SCIM (P6.9) -------------------------------------------------------------------------------------------


def scim_client(app: Any) -> TestClient:
    token = as_user(app, "admin").post("/api/tokens", json={"name": "scim"}, headers=CSRF).json()["token"]
    return TestClient(app, headers={"Authorization": f"Bearer {token}", "Content-Type": SCIM})


def test_scim_users_lifecycle(app: Any) -> None:
    scim = scim_client(app)
    assert TestClient(app).get("/scim/v2/Users").status_code == 401
    created = scim.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "nina@corp.example",
            "name": {"givenName": "Nina", "familyName": "Ng"},
            "emails": [{"value": "nina@corp.example", "primary": True}],
            "urn:tabfree:scim:User": {"siteRole": "explorer"},
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["displayName"] == "Nina Ng"
    assert user["urn:tabfree:scim:User"]["siteRole"] == "explorer"
    assert scim.post("/scim/v2/Users", json={"userName": "nina@corp.example"}).status_code == 409
    found = scim.get("/scim/v2/Users", params={"filter": 'userName eq "NINA@corp.example"'}).json()
    assert found["totalResults"] == 1
    assert found["Resources"][0]["id"] == user["id"]
    assert (
        scim.get("/scim/v2/Users", params={"filter": 'userName sw "n"'}).json()["scimType"] == "invalidFilter"
    )
    patched = scim.patch(
        f"/scim/v2/Users/{user['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    assert patched.json()["active"] is False
    assert app.state.context.site.users.get("nina@corp.example") is None  # disabled users cannot sign in
    assert scim.delete(f"/scim/v2/Users/{user['id']}").status_code == 204
    assert scim.get("/scim/v2/Users/nope").json()["status"] == "404"
    page = scim.get("/scim/v2/Users", params={"startIndex": 2, "count": 3}).json()
    assert page["startIndex"] == 2
    assert page["itemsPerPage"] == 3


def test_scim_groups_and_membership(app: Any) -> None:
    scim = scim_client(app)
    users = {
        u["userName"]: u["id"] for u in scim.get("/scim/v2/Users", params={"count": 100}).json()["Resources"]
    }
    group = scim.post(
        "/scim/v2/Groups", json={"displayName": "Finance", "members": [{"value": users["alice"]}]}
    )
    assert group.status_code == 201
    gid = group.json()["id"]
    op = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
    scim.patch(
        f"/scim/v2/Groups/{gid}",
        json={
            "schemas": [op],
            "Operations": [{"op": "add", "path": "members", "value": [{"value": users["bob"]}]}],
        },
    )
    scim.patch(
        f"/scim/v2/Groups/{gid}",
        json={
            "schemas": [op],
            "Operations": [{"op": "remove", "path": f'members[value eq "{users["alice"]}"]'}],
        },
    )
    renamed = scim.patch(
        f"/scim/v2/Groups/{gid}",
        json={
            "schemas": [op],
            "Operations": [{"op": "replace", "path": "displayName", "value": "Finance EMEA"}],
        },
    )
    assert renamed.json()["displayName"] == "Finance EMEA"
    assert [m["display"] for m in renamed.json()["members"]] == ["bob"]
    assert "Finance EMEA" in app.state.context.site.users["bob"].groups
    assert (
        scim.get("/scim/v2/Groups", params={"filter": 'displayName eq "Finance EMEA"'}).json()["totalResults"]
        == 1
    )
    assert scim.delete(f"/scim/v2/Groups/{gid}").status_code == 204
    assert scim.get("/scim/v2/ServiceProviderConfig").json()["patch"]["supported"] is True
    assert as_user(app, "erin").get("/scim/v2/Users").status_code == 403
