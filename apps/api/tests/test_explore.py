"""P2.7 semantic query API: POST /api/models/{model}/query with Connect permission and RLS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.authz import Capability, ModelEntry, Principal, SiteRole, decide_model, parse_rules
from tabfree_api.cache import MemoryResultCache, NoCache
from tabfree_api.content import ContentError, Site
from tabfree_api.guardrails import Guardrails, SiteLimits
from tabfree_api.main import create_app, query_service
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")

CSRF = {"X-TabFree-Request": "1"}
BY_REGION = {"dimensions": ["Region"], "measures": ["Sales"], "sort": [{"by": "Region", "dir": "asc"}]}


@pytest.fixture
def app(tmp_path: Path) -> Any:
    return create_app(Settings(environment="test", dev_login=True, session_db=tmp_path / "s.db"))


def signed_in(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/dev", content=f"user={user}", follow_redirects=False)
    assert response.status_code == 303, response.text
    return client


def explore(client: TestClient, query: Any, model: str = "superstore_sales") -> Any:
    return client.post(f"/api/models/{model}/query", json={"query": query}, headers=CSRF)


def test_explorers_with_connect_query_the_model_and_hit_the_cache(app: Any) -> None:
    erin = signed_in(app, "erin")  # explorer, Executives: connect allowed, RLS-exempt
    first = explore(erin, BY_REGION)
    assert first.status_code == 200, first.text
    body = first.json()
    assert [c["name"] for c in body["columns"]] == ["Region", "Sales"]
    assert [r[0] for r in body["rows"]] == ["Central", "East", "South", "West"]
    assert body["meta"]["cache"] == "miss"
    assert body["truncated"] is False
    again = explore(erin, BY_REGION).json()
    assert again["meta"]["cache"] == "hit"
    assert again["rows"] == body["rows"]


def test_rls_applies_to_explore_queries(app: Any) -> None:
    bob = signed_in(app, "bob")  # explorer in Managers: sees the West only
    rows = explore(bob, BY_REGION).json()["rows"]
    assert [r[0] for r in rows] == ["West"]
    # The executives' cached result for the same query must not leak to bob.
    explore(signed_in(app, "erin"), BY_REGION)
    assert [r[0] for r in explore(bob, BY_REGION).json()["rows"]] == ["West"]


@pytest.mark.parametrize(
    ("user", "model", "status"),
    [
        ("alice", "superstore_sales", 404),  # Managers allow, but the viewer role's ceiling denies
        ("ivan", "superstore_sales", 404),  # no rule: default deny
        ("bob", "superstore", 404),  # only Executives may connect to this one
        ("erin", "superstore", 200),
        ("erin", "no-such-model", 404),
        ("admin", "superstore_sales", 200),  # site administrators may do everything
    ],
)
def test_connect_permission(app: Any, user: str, model: str, status: int) -> None:
    query = {"measures": ["Sales"]}
    assert explore(signed_in(app, user), query, model).status_code == status


def test_explore_requests_are_validated(app: Any) -> None:
    erin = signed_in(app, "erin")
    assert explore(erin, {}).status_code == 400
    assert explore(erin, {"dimensions": ["Region"], "limit": 0}).status_code == 400
    assert explore(erin, {"dimensions": ["Nope"]}).status_code == 400  # unknown field
    response = explore(erin, {"dimensions": ["Region"], "where": "x"})
    assert response.status_code == 400
    assert "invalid query" in response.json()["detail"]
    no_csrf = erin.post("/api/models/superstore_sales/query", json={"query": BY_REGION})
    assert no_csrf.status_code == 403
    anonymous = TestClient(app).post(
        "/api/models/superstore_sales/query", json={"query": BY_REGION}, headers=CSRF
    )
    assert anonymous.status_code == 401


def test_over_budget_sites_get_429_with_a_code(app: Any) -> None:
    service = app.state.context.service
    service.guardrails = Guardrails(lambda _site: SiteLimits(0, 4, 0.0))
    erin = signed_in(app, "erin")
    response = explore(erin, {"measures": ["Sales", "Profit"]})
    assert response.status_code == 429
    assert response.json()["code"] == "scan_budget_exceeded"
    assert "daily query budget" in response.json()["detail"]
    assert int(response.headers["Retry-After"]) > 0
    dashboard = erin.post(
        "/api/dashboards/sales-overview/query",
        json={"query": "kpis", "state": {"filters": {}, "parameters": {}, "highlight": None}},
        headers=CSRF,
    )
    assert dashboard.status_code == 429
    assert set(dashboard.json()) == {"detail", "code"}


# ---- site file and settings ---------------------------------------------------------------------


def test_site_file_query_and_model_sections(tmp_path: Path) -> None:
    site = Site(Settings().site_file)
    assert site.id == "dev"
    assert site.query.athena_workgroup == "tabfree-dev"
    assert site.query.daily_scan_budget_bytes == 100 * 1024**3
    assert site.data_version("superstore_sales") == "2024-12-31"
    assert site.data_version("unknown") == ""

    service = query_service(Settings(), site, lambda _model: None)
    assert service.site == "dev"
    assert isinstance(service.cache, MemoryResultCache)
    off = query_service(Settings(result_cache_entries=0, query_row_limit=5), site, lambda _model: None)
    assert isinstance(off.cache, NoCache)
    assert off.limits.row_limit == 5

    examples = Settings().site_file.parent.parent.parent.parent / "packages" / "schemas" / "examples"
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(f"dashboards: {examples / 'dashboards'}\nmodels: {examples / 'models'}\n")
    old = Site(legacy)
    assert old.id == "default"
    assert old.query.daily_scan_budget_bytes is None
    assert old.model_entries == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        f"dashboards: {examples / 'dashboards'}\nmodels:\n  path: {examples / 'models'}\n"
        "  entries: [{id: nope}]\n"
    )
    with pytest.raises(ContentError, match="unknown model"):
        Site(bad)


def test_model_decision_order() -> None:
    rules = parse_rules(
        [
            {"group": "A", "capabilities": {"connect": "allow"}},
            {"group": "B", "capabilities": {"connect": "deny"}},
        ]
    )
    model = ModelEntry("m", "owner", rules)

    def who(user: str, *groups: str, role: SiteRole = SiteRole.EXPLORER) -> Principal:
        return Principal(user, frozenset(groups), role)

    assert decide_model(who("x", "A"), Capability.CONNECT, model) is True
    assert decide_model(who("x", "A", "B"), Capability.CONNECT, model) is False  # deny wins
    assert decide_model(who("x", "A", role=SiteRole.VIEWER), Capability.CONNECT, model) is False
    assert decide_model(who("owner"), Capability.CONNECT, model) is True
    assert decide_model(who("x", "C"), Capability.CONNECT, model) is False
    assert decide_model(who("x", "A"), Capability.CONNECT, ModelEntry("m", None, ())) is False
