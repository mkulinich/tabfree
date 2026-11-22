"""Governed dashboard API: permissions (ADR-006 order) and row-level security per signed-in user."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.authz import parse_rules
from tabfree_api.main import create_app
from tabfree_api.sample import MANAGERS
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")

STATE: dict[str, Any] = {"filters": {}, "parameters": {}, "highlight": None}
CSRF = {"X-TabFree-Request": "1"}


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Any:
    db = tmp_path_factory.mktemp("sessions") / "s.db"
    # The minimal built-in page (no portal build), so these tests do not depend on `npm run build`.
    return create_app(
        Settings(environment="test", dev_login=True, session_db=db, portal_dir=db.parent / "none")
    )


def signed_in(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/dev", content=f"user={user}", follow_redirects=False)
    assert response.status_code == 303, response.text
    return client


def run(client: TestClient, query: str, **extra: Any) -> Any:
    return client.post(
        "/api/dashboards/sales-overview/query", json={"query": query, "state": STATE, **extra}, headers=CSRF
    )


def test_anonymous_requests_need_sign_in(app: Any) -> None:
    client = TestClient(app)
    assert client.get("/api/dashboards").status_code == 401
    assert run(client, "kpis").status_code == 401
    assert "Sign in" in client.get("/").text


ALL = ["dimensional-hierarchy", "sales-analysis", "sales-overview"]


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ("admin", ALL),  # site administrator
        ("erin", ALL),  # Executives: allowed in both projects
        ("alice", ["sales-analysis", "sales-overview"]),  # Managers: the Sales project only
        ("mallory", []),  # Managers allow + Contractors deny: deny wins
        ("ivan", []),  # no rule applies: unspecified is denied
    ],
)
def test_view_permission(app: Any, user: str, expected: list[str]) -> None:
    client = signed_in(app, user)
    listed = [d["id"] for d in client.get("/api/dashboards").json()]
    assert listed == expected
    visible = "sales-overview" in expected
    assert client.get("/api/dashboards/sales-overview").status_code == (200 if visible else 404)
    assert run(client, "kpis").status_code == (200 if visible else 404)


def test_row_level_security_per_signed_in_manager(app: Any) -> None:
    for region, manager in MANAGERS.items():
        rows = run(signed_in(app, manager), "by_region").json()["rows"]
        assert [r[0] for r in rows] == [region]
    exec_rows = run(signed_in(app, "erin"), "by_region").json()["rows"]
    assert {r[0] for r in exec_rows} == set(MANAGERS)


def test_requests_without_the_csrf_header_are_refused(app: Any) -> None:
    client = signed_in(app, "erin")
    response = client.post("/api/dashboards/sales-overview/query", json={"query": "kpis", "state": STATE})
    assert response.status_code == 403


def test_bad_queries_and_domains(app: Any) -> None:
    client = signed_in(app, "erin")
    assert run(client, "nope").status_code == 400
    bad_state = {**STATE, "filters": {"region": {"members": ["x"] * 20_000}}}
    assert run(client, "kpis", state=bad_state).status_code in (400, 422)
    domain = client.post(
        "/api/dashboards/sales-overview/domain", json={"filter": "region", "state": STATE}, headers=CSRF
    )
    assert [r[0] for r in domain.json()["rows"]] == sorted(MANAGERS)


def test_portal_page_and_runtime_assets(app: Any) -> None:
    client = signed_in(app, "erin")
    page = client.get("/").text
    assert "/runtime/host.js" in page
    assert "Erin (executive)" in page
    assert client.get("/runtime/secret.txt").status_code == 404


def test_dev_login_is_refused_outside_development(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="dev_login"):
        create_app(
            Settings(environment="production", dev_login=True, engine="aws", session_db=tmp_path / "s.db")
        )
    app = create_app(Settings(environment="test", session_db=tmp_path / "s.db"))
    assert TestClient(app).post("/auth/dev", content="user=admin").status_code == 404


def test_rules_must_be_well_formed() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        parse_rules([{"user": "a", "group": "b", "capabilities": {}}])
    with pytest.raises(ValueError, match="allow"):
        parse_rules([{"user": "a", "capabilities": {"view": "maybe"}}])


def test_the_built_portal_is_served_for_its_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>')
    (dist / "assets" / "app.js").write_text("console.log(1)")
    app = create_app(Settings(environment="test", session_db=tmp_path / "s.db", portal_dir=dist))
    client = TestClient(app)
    for path in ("/", "/explore", "/dashboards/sales-overview", "/models/x", "/admin/users", "/account"):
        page = client.get(path)
        assert page.status_code == 200
        assert '<div id="root">' in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/assets/app.js").headers["cache-control"].startswith("public")
    assert client.get("/assets/../index.html").status_code == 404
    assert client.get("/assets/nope.js").status_code == 404
