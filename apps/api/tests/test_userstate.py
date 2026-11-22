"""Phase 6: user state (P6.5) and the content lifecycle (P6.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.jobs.mail import LogMailer
from tabfree_api.lifecycle import purge_expired
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

CSRF = {"X-TabFree-Request": "1"}
STATE = {"filters": {"region": {"members": ["East"]}}, "parameters": {"top_n": 5}}


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    engine = connect(url)
    upgrade(engine)
    db = import_site(engine, Site(Settings().site_file))
    for name in ("erin", "casey", "alice", "ivan"):
        db.ensure_user(name, name, email=f"{name}@corp.example")
    application = create_app(
        Settings(
            environment="test",
            dev_login=True,
            database_url=url,
            site_id="dev",
            permission_cache_s=0,
            base_url="http://testserver",
        )
    )
    application.state.context.mailer = LogMailer()
    return application


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def test_custom_views_private_shared_and_default(app: Any) -> None:
    erin, alice, admin = as_user(app, "erin"), as_user(app, "alice"), as_user(app, "admin")
    mine = erin.post(
        "/api/dashboards/sales-overview/views",
        json={"name": "East", "state": STATE, "default": True},
        headers=CSRF,
    )
    assert mine.status_code == 201
    # Sharing needs Share Customized, which no rule grants Erin.
    shared = erin.post(
        "/api/dashboards/sales-overview/views",
        json={"name": "Team", "state": STATE, "shared": True},
        headers=CSRF,
    )
    assert shared.status_code == 403
    listed = erin.get("/api/dashboards/sales-overview/views").json()
    assert [v["name"] for v in listed["views"]] == ["East"]
    assert listed["default"] == mine.json()["id"]
    assert alice.get("/api/dashboards/sales-overview/views").json() == {"views": [], "default": None}
    team = admin.post(
        "/api/dashboards/sales-overview/views",
        json={"name": "Team", "state": STATE, "shared": True},
        headers=CSRF,
    )
    assert team.status_code == 201
    assert [v["name"] for v in alice.get("/api/dashboards/sales-overview/views").json()["views"]] == ["Team"]
    # Alice may make the shared view her default but not change it.
    view = team.json()["id"]
    assert alice.put(f"/api/custom-views/{view}/default", headers=CSRF).status_code == 200
    assert (
        alice.patch(f"/api/custom-views/{view}", json={"name": "Mine now"}, headers=CSRF).status_code == 404
    )
    assert alice.get("/api/dashboards/sales-overview/views").json()["default"] == view
    bad = erin.post(
        "/api/dashboards/sales-overview/views", json={"name": "x", "state": {"sql": "DROP"}}, headers=CSRF
    )
    assert bad.status_code == 422
    assert as_user(app, "ivan").get("/api/dashboards/sales-overview/views").status_code == 404


def test_sharing_links_grant_nothing(app: Any) -> None:
    erin = as_user(app, "erin")
    link = erin.post("/api/dashboards/sales-overview/share", json={"state": STATE}, headers=CSRF).json()
    assert link["url"].startswith("http://testserver/s/")
    assert erin.get(f"/api/share/{link['id']}").json() == {"dashboard": "sales-overview", "state": STATE}
    assert as_user(app, "ivan").get(f"/api/share/{link['id']}").status_code == 404
    redirect = TestClient(app).get(f"/s/{link['id']}", follow_redirects=False)
    assert redirect.headers["location"] == f"/?share={link['id']}"


def test_comments_and_mentions(app: Any) -> None:
    ctx = app.state.context
    erin = as_user(app, "erin")
    refused = erin.post("/api/dashboards/sales-overview/comments", json={"body": "hi"}, headers=CSRF)
    assert refused.status_code == 403  # the dev site grants nobody Add Comments yet
    from tabfree_api.authz import Capability, Rule

    db = ctx.site
    rules = [
        Rule(
            r.user,
            r.group,
            {**r.capabilities, Capability.VIEW_COMMENTS: "allow", Capability.ADD_COMMENTS: "allow"},
        )
        if r.group in ("Executives", "Managers")
        else r
        for r in db.projects["sales"].defaults["dashboard"]
    ]
    db.set_rules("project", "sales", rules, content_type="dashboard")
    posted = erin.post(
        "/api/dashboards/sales-overview/comments",
        json={"body": "East looks low, @alice and @ivan and @nobody"},
        headers=CSRF,
    ).json()
    # Ivan cannot see the dashboard, so only Alice is told (with a link, never data).
    assert posted["mentions"] == ["alice", "ivan"]
    assert posted["notified"] == ["alice"]
    (mail,) = ctx.mailer.sent
    assert mail.to == ["alice@corp.example"]
    assert "East looks low" not in mail.text
    comments = as_user(app, "alice").get("/api/dashboards/sales-overview/comments").json()
    assert comments[0]["body"].startswith("East looks low")
    assert as_user(app, "alice").delete(f"/api/comments/{posted['id']}", headers=CSRF).status_code == 403
    assert erin.delete(f"/api/comments/{posted['id']}", headers=CSRF).status_code == 204
    assert erin.get("/api/dashboards/sales-overview/comments").json() == []


def test_tags_and_collections_follow_permissions(app: Any) -> None:
    erin, casey, ivan = as_user(app, "erin"), as_user(app, "casey"), as_user(app, "ivan")
    assert (
        erin.put(
            "/api/content/dashboard/sales-overview/tags", json={"tags": ["finance"]}, headers=CSRF
        ).status_code
        == 403
    )
    tags = casey.put(
        "/api/content/dashboard/sales-overview/tags",
        json={"tags": ["Finance", " weekly ", "finance"]},
        headers=CSRF,
    )
    assert tags.json() == ["finance", "weekly"]
    assert (
        casey.put(
            "/api/content/dashboard/sales-overview/tags", json={"tags": ["<b>"]}, headers=CSRF
        ).status_code
        == 422
    )
    assert erin.get("/api/tags/finance").json() == [{"kind": "dashboard", "id": "sales-overview"}]
    assert ivan.get("/api/tags/finance").json() == []
    made = erin.post(
        "/api/collections",
        json={
            "name": "Mine",
            "shared": True,
            "items": [{"kind": "dashboard", "id": "sales-overview"}, {"kind": "dashboard", "id": "secret"}],
        },
        headers=CSRF,
    )
    assert made.status_code == 201
    (collection,) = erin.get("/api/collections").json()
    assert collection["items"] == [{"kind": "dashboard", "id": "sales-overview"}]  # unknown items dropped
    # Shared, but Ivan sees none of its items: sharing a collection shares no content.
    assert ivan.get("/api/collections").json()[0]["items"] == []
    assert ivan.delete(f"/api/collections/{collection['id']}", headers=CSRF).status_code == 404


def test_recycle_bin_move_copy_owner_and_certification(app: Any) -> None:
    admin, erin, priya = as_user(app, "admin"), as_user(app, "erin"), as_user(app, "priya")
    assert erin.delete("/api/content/dashboard/sales-analysis", headers=CSRF).status_code == 403
    assert admin.delete("/api/content/dashboard/sales-analysis", headers=CSRF).status_code == 204
    assert erin.get("/api/dashboards/sales-analysis").status_code == 404
    (deleted,) = admin.get("/api/recycle-bin").json()
    assert deleted["id"] == "sales-analysis"
    assert "purgeAfter" in deleted
    assert erin.get("/api/recycle-bin").json() == []
    assert admin.post("/api/recycle-bin/dashboard/sales-analysis/restore", headers=CSRF).status_code == 200
    assert erin.get("/api/dashboards/sales-analysis").status_code == 200
    # Move: Move on the content and Publish on the target.
    assert (
        admin.post(
            "/api/content/dashboard/sales-analysis/move", json={"project": "nowhere"}, headers=CSRF
        ).status_code
        == 404
    )
    moved = admin.post(
        "/api/content/dashboard/sales-analysis/move", json={"project": "migrated"}, headers=CSRF
    )
    assert moved.json() == {"project": "migrated"}
    # Copy (Save As): Download Workbook on the source, Publish on the target.
    assert (
        erin.post(
            "/api/content/dashboard/sales-overview/copy",
            json={"id": "mine", "project": "sales"},
            headers=CSRF,
        ).status_code
        == 403
    )
    copied = admin.post(
        "/api/content/dashboard/sales-overview/copy",
        json={"id": "sales-overview-copy", "project": "sales"},
        headers=CSRF,
    )
    assert copied.status_code == 201
    assert admin.get("/api/dashboards/sales-overview-copy").json()["manifest"]["title"].endswith("(copy)")
    assert (
        admin.post(
            "/api/content/dashboard/sales-overview/copy",
            json={"id": "sales-overview-copy", "project": "sales"},
            headers=CSRF,
        ).status_code
        == 409
    )
    # Owner, certification and warnings: project leaders and administrators.
    assert (
        erin.post(
            "/api/content/dashboard/sales-overview/owner", json={"owner": "erin"}, headers=CSRF
        ).status_code
        == 403
    )
    assert priya.post(
        "/api/content/dashboard/sales-overview/owner", json={"owner": "erin"}, headers=CSRF
    ).json() == {"owner": "erin"}
    certified = priya.post(
        "/api/content/dashboard/sales-overview/certification",
        json={"certified": True, "note": "Finance-reviewed"},
        headers=CSRF,
    )
    assert certified.json()["certifiedBy"] == "priya"
    warned = erin.put(
        "/api/content/dashboard/sales-overview/quality-warning",
        json={"type": "stale", "message": "Q3 data is late"},
        headers=CSRF,
    )
    assert warned.status_code == 200  # Erin owns it now
    quality = as_user(app, "alice").get("/api/content/dashboard/sales-overview/quality").json()
    assert quality["certified"] is True
    assert quality["warning"]["type"] == "stale"
    # Purging: administrators only, and the bin empties itself after the retention period.
    admin.delete("/api/content/dashboard/sales-overview-copy", headers=CSRF)
    assert erin.delete("/api/recycle-bin/dashboard/sales-overview-copy", headers=CSRF).status_code == 403
    assert purge_expired(app.state.context.site, days=0) == 1
    assert admin.get("/api/recycle-bin").json() == []


def test_usage_report_counts_views_and_query_latency(app: Any) -> None:
    erin, admin = as_user(app, "erin"), as_user(app, "admin")
    assert erin.get("/api/dashboards/sales-overview").status_code == 200
    ran = erin.post(
        "/api/dashboards/sales-overview/query", json={"query": "by_region", "state": {}}, headers=CSRF
    )
    assert ran.status_code == 200
    assert erin.get("/api/admin/usage").status_code == 403
    report = admin.get("/api/admin/usage", params={"days": 7}).json()
    top = report["topDashboards"][0]
    assert top["dashboard"] == "sales-overview"
    assert top["views"] == 1
    assert top["viewers"] == 1
    assert top["p50Ms"] is not None
    assert report["queries"]["count"] >= 1
    assert report["activeUsers"] >= 1
    assert report["storage"]["dashboards"] >= 3
