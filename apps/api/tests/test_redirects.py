"""P3.9: old Tableau links reach the dashboard that replaced the view; P3.8: queries are audited."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.db.store import AuditLog
from tabfree_api.main import create_app
from tabfree_api.redirects import normalise
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (
            "https://tableau.example.com/#/site/Finance/views/Superstore/Overview?:iid=1",
            "views/Superstore/Overview",
        ),
        ("/#/views/Superstore/Overview", "views/Superstore/Overview"),
        ("#/site/x/views/Super%20Store/Sales%20Map", "views/Super Store/Sales Map"),
        ("/t/Finance/views/Superstore/Overview?:embed=y&:showVizHome=no", "views/Superstore/Overview"),
        ("/views/Superstore/Overview/", "views/Superstore/Overview"),
        ("/views/Superstore/Overview/alice@example.com/My%20view", "views/Superstore/Overview"),
        ("/views/Superstore", None),
        ("/views/../etc/passwd", None),
        ("/workbooks/123", None),
    ],
)
def test_normalise(link: str, expected: str | None) -> None:
    assert normalise(link) == expected


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'r.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    return create_app(
        Settings(
            environment="test", dev_login=True, database_url=url, site_id="dev", portal_dir=tmp_path / "none"
        )
    )


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def test_old_links_redirect_for_those_who_may_see_them(app: Any) -> None:
    admin, erin, mallory = as_user(app, "admin"), as_user(app, "erin"), as_user(app, "mallory")
    body = {
        "path": "https://tableau.example.com/#/site/Sales/views/Superstore/Overview",
        "id": "sales-overview",
    }
    assert admin.put("/api/admin/legacy-urls", json=body, headers=CSRF).status_code == 204
    assert admin.get("/api/admin/legacy-urls").json() == [
        {"path": "views/Superstore/Overview", "kind": "dashboard", "id": "sales-overview"}
    ]
    bad = admin.put("/api/admin/legacy-urls", json={"path": "/nope", "id": "sales-overview"}, headers=CSRF)
    assert bad.status_code == 422
    assert erin.put("/api/admin/legacy-urls", json=body, headers=CSRF).status_code == 403
    # Share and embed links: a server redirect.
    hop = erin.get("/t/Sales/views/Superstore/Overview?:embed=y", follow_redirects=False)
    assert (hop.status_code, hop.headers["location"]) == (302, "/dashboards/sales-overview")
    assert erin.get("/views/Superstore/Overview", follow_redirects=False).status_code == 302
    # Portal (#) links: the SPA asks.
    resolved = erin.get("/api/redirects/resolve", params={"path": "#/site/Sales/views/Superstore/Overview"})
    assert resolved.json() == {"to": "/dashboards/sales-overview"}
    # Not visible to Mallory: the same answer as an unknown link.
    assert mallory.get("/views/Superstore/Overview", follow_redirects=False).status_code == 404
    assert (
        mallory.get("/api/redirects/resolve", params={"path": "/views/Superstore/Overview"}).status_code
        == 404
    )
    assert erin.get("/views/Unknown/Sheet", follow_redirects=False).status_code == 404
    # Signed out: sign in first, then come back to the same link.
    anonymous = TestClient(app).get("/views/Superstore/Overview?:iid=1", follow_redirects=False)
    assert anonymous.headers["location"] == "/auth/login?return=/views/Superstore/Overview"


def test_queries_are_audited_without_values(app: Any) -> None:
    erin = as_user(app, "erin")
    state = {"filters": {"region": {"members": ["East"]}}, "parameters": {}, "highlight": None}
    response = erin.post(
        "/api/dashboards/sales-overview/query", json={"query": "kpis", "state": state}, headers=CSRF
    )
    assert response.status_code == 200
    engine = app.state.context.audit_log.engine
    [event] = AuditLog(engine, "dev").events(action="query")
    assert event["actor"] == "erin"
    assert event["detail"]["query"] == "kpis"
    assert len(event["detail"]["request"]) == 16
    assert "East" not in str(event)
