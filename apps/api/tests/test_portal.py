"""Governed dashboard API: permissions (ADR-006 order) and row-level security per signed-in user."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.authz import Capability, DashboardEntry, Principal, Project, SiteRole, decide, parse_rules
from tabfree_api.main import create_app
from tabfree_api.sample import MANAGERS
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")

STATE: dict[str, Any] = {"filters": {}, "parameters": {}, "highlight": None}
CSRF = {"X-TabFree-Request": "1"}


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Any:
    db = tmp_path_factory.mktemp("sessions") / "s.db"
    return create_app(Settings(environment="test", dev_login=True, session_db=db))


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


@pytest.mark.parametrize(
    ("user", "visible"),
    [
        ("admin", True),  # site administrator
        ("erin", True),  # Executives: allow
        ("alice", True),  # Managers: allow
        ("mallory", False),  # Managers allow + Contractors deny: deny wins
        ("ivan", False),  # no rule applies: unspecified is denied
    ],
)
def test_view_permission(app: Any, user: str, visible: bool) -> None:
    client = signed_in(app, user)
    listed = [d["id"] for d in client.get("/api/dashboards").json()]
    assert listed == (["sales-overview"] if visible else [])
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


# ---- the decision order itself ---------------------------------------------------------------


def principal(user: str, *groups: str, role: SiteRole = SiteRole.EXPLORER) -> Principal:
    return Principal(user, frozenset(groups), role)


RULES = parse_rules(
    [
        {"group": "A", "capabilities": {"view": "allow", "download_full_data": "allow"}},
        {"group": "B", "capabilities": {"view": "deny"}},
        {"user": "uma", "capabilities": {"view": "allow"}},
        {"user": "dan", "capabilities": {"view": "deny"}},
    ]
)


@pytest.mark.parametrize(
    ("who", "capability", "expected"),
    [
        (principal("x", "A"), Capability.VIEW, True),
        (principal("x", "A", "B"), Capability.VIEW, False),  # group deny wins over group allow
        (principal("uma", "B"), Capability.VIEW, True),  # a user rule beats group rules
        (principal("dan", "A"), Capability.VIEW, False),
        (principal("x", "A", role=SiteRole.VIEWER), Capability.DOWNLOAD_FULL, False),  # site-role ceiling
        (principal("x", "A"), Capability.DOWNLOAD_FULL, True),
        (principal("x", "C"), Capability.VIEW, False),  # unspecified
        (principal("root", role=SiteRole.SITE_ADMINISTRATOR), Capability.DELETE, True),
        (principal("owner", "B"), Capability.VIEW, True),  # owners can always view their content
    ],
)
def test_decision_order(who: Principal, capability: Capability, expected: bool) -> None:
    project = Project("p", "P", locked=True, rules=RULES)
    dashboard = DashboardEntry("d", "p", owner="owner", rules=())
    assert decide(who, capability, dashboard, project) is expected


def test_unlocked_projects_let_dashboards_override() -> None:
    own = parse_rules([{"group": "B", "capabilities": {"view": "allow"}}])
    dashboard = DashboardEntry("d", "p", owner=None, rules=own)
    unlocked = Project("p", "P", locked=False, rules=RULES)
    locked = Project("p", "P", locked=True, rules=RULES)
    assert decide(principal("x", "B"), Capability.VIEW, dashboard, unlocked) is True
    assert decide(principal("x", "B"), Capability.VIEW, dashboard, locked) is False


def test_rules_must_be_well_formed() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        parse_rules([{"user": "a", "group": "b", "capabilities": {}}])
    with pytest.raises(ValueError, match="allow"):
        parse_rules([{"user": "a", "capabilities": {"view": "maybe"}}])
