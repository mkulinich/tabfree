"""P3.5/P3.8 portal APIs: browse and search, favorites and recents, the model browser, revisions,
the permissions dialog, projects and site administration — on the imported dev site."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'portal.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    return create_app(
        Settings(environment="test", dev_login=True, database_url=url, site_id="dev", permission_cache_s=0)
    )


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def test_browse_is_permission_filtered(app: Any) -> None:
    erin, mallory, admin = as_user(app, "erin"), as_user(app, "mallory"), as_user(app, "admin")
    erin_ids = {d["id"] for d in erin.get("/api/content").json()}
    assert {"sales-overview", "sales-analysis", "dimensional-hierarchy", "superstore_sales"} <= erin_ids
    assert {d["id"] for d in mallory.get("/api/content", params={"kind": "dashboard"}).json()} == set()
    assert len(admin.get("/api/content").json()) >= len(erin_ids)
    found = erin.get("/api/content", params={"q": "sales overview"}).json()
    assert [d["id"] for d in found] == ["sales-overview"]
    within = erin.get("/api/content", params={"project": "migrated"}).json()
    assert {d["id"] for d in within} == {"dimensional-hierarchy"}
    tree = {p["id"]: p for p in erin.get("/api/projects").json()}
    assert tree["sales"]["assetPermissions"] == "locked"
    assert tree["sales"]["canManage"] is False
    assert {p["id"] for p in admin.get("/api/projects").json()} >= {"sales", "migrated"}
    assert admin.get("/api/site").json()["admin"] is True


def test_home_favorites_and_recents(app: Any) -> None:
    erin = as_user(app, "erin")
    assert erin.put("/api/favorites/dashboard/sales-analysis", headers=CSRF).status_code == 204
    erin.get("/api/dashboards/sales-overview")  # a view, recorded in the audit log
    home = erin.get("/api/home").json()
    assert [d["id"] for d in home["favorites"]] == ["sales-analysis"]
    assert home["favorites"][0]["favorite"] is True
    assert [d["id"] for d in home["recents"]] == ["sales-overview"]
    assert erin.delete("/api/favorites/dashboard/sales-analysis", headers=CSRF).status_code == 204
    assert erin.get("/api/home").json()["favorites"] == []
    mallory = as_user(app, "mallory")
    assert mallory.put("/api/favorites/dashboard/sales-analysis", headers=CSRF).status_code == 404


def test_dashboard_info_and_model_browser(app: Any) -> None:
    erin, bob = as_user(app, "erin"), as_user(app, "bob")
    info = erin.get("/api/dashboards/sales-overview/info").json()
    assert info["capabilities"]["view"] is True
    assert info["capabilities"]["download_summary_data"] is True
    assert info["capabilities"]["overwrite"] is False
    assert info["locked"] is True
    model = erin.get("/api/models/superstore_sales").json()
    names = {f["name"] for f in model["fields"]}
    assert {"Sales", "Region"} <= names
    assert "connection" not in model
    assert "rls" not in model  # policy names only for those who may change the model
    admin_view = as_user(app, "admin").get("/api/models/superstore_sales").json()
    assert admin_view["rls"]
    assert bob.get("/api/models/superstore").status_code == 404


def test_revisions_need_overwrite(app: Any) -> None:
    admin, erin = as_user(app, "admin"), as_user(app, "erin")
    history = admin.get("/api/content/dashboard/sales-overview/revisions").json()
    assert [r["number"] for r in history] == [1]
    assert erin.get("/api/content/dashboard/sales-overview/revisions").status_code == 403
    assert admin.post("/api/content/dashboard/sales-overview/revisions/1/restore", headers=CSRF).json() == {
        "revision": 1
    }


def test_permissions_dialog(app: Any) -> None:
    admin, erin = as_user(app, "admin"), as_user(app, "erin")
    project = admin.get("/api/permissions/project/sales").json()
    assert project["assetPermissions"] == "locked"
    assert {r["group"] for r in project["defaults"]["dashboard"]} == {"Executives", "Managers", "Contractors"}
    assert erin.get("/api/permissions/project/sales").status_code == 404
    # Locked project: the dashboard's own rules cannot change.
    locked = admin.put(
        "/api/permissions/dashboard/sales-overview",
        json={"rules": [{"group": "Managers", "capabilities": {"view": "allow"}}]},
        headers=CSRF,
    )
    assert locked.status_code == 409
    # Change the project's defaults instead: Managers lose View.
    defaults = [
        {"group": "Executives", "capabilities": {"view": "allow", "download_summary_data": "allow"}},
        {"group": "Managers", "capabilities": {"view": "deny"}},
    ]
    assert (
        admin.put(
            "/api/permissions/project/sales",
            json={"rules": defaults, "contentType": "dashboard"},
            headers=CSRF,
        ).status_code
        == 204
    )
    alice = as_user(app, "alice")
    assert alice.get("/api/dashboards/sales-overview").status_code == 404
    effective = admin.get(
        "/api/permissions/dashboard/sales-overview/effective", params={"user": "alice"}
    ).json()
    assert effective["capabilities"]["view"] == {
        "allowed": False,
        "reason": "group 'Managers' denied in locked project 'Sales'",
    }
    bad = admin.put(
        "/api/permissions/project/sales",
        json={"rules": [{"group": "X", "capabilities": {"connect": "allow"}}], "contentType": "dashboard"},
        headers=CSRF,
    )
    assert bad.status_code == 422
    leader = [{"user": "bob", "capabilities": {"project_leader": "allow"}}]
    response = admin.put(
        "/api/permissions/project/sales", json={"rules": leader, "contentType": "project"}, headers=CSRF
    )
    assert response.status_code == 204
    # Bob is an Explorer: leadership cannot lift him above his site role's ceiling.
    bob_caps = admin.get("/api/permissions/dashboard/sales-overview/effective", params={"user": "bob"}).json()
    assert bob_caps["capabilities"]["view"]["reason"] == "leader of project 'Sales'"
    assert bob_caps["capabilities"]["delete"]["allowed"] is False


def test_projects_create_update_delete(app: Any) -> None:
    admin, erin = as_user(app, "admin"), as_user(app, "erin")
    assert erin.post("/api/projects", json={"id": "x", "name": "X"}, headers=CSRF).status_code == 403
    assert (
        admin.post("/api/projects", json={"id": "finance", "name": "Finance"}, headers=CSRF).status_code
        == 201
    )
    assert (
        admin.post("/api/projects", json={"id": "finance", "name": "Again"}, headers=CSRF).status_code == 409
    )
    nested = {"id": "budgets", "name": "Budgets", "parent": "finance"}
    assert admin.post("/api/projects", json=nested, headers=CSRF).status_code == 201
    patch = {"assetPermissions": "locked_not_nested", "owner": "erin"}
    assert admin.patch("/api/projects/finance", json=patch, headers=CSRF).status_code == 204
    tree = {p["id"]: p for p in admin.get("/api/projects").json()}
    assert tree["budgets"]["path"] == ["Finance", "Budgets"]
    assert tree["finance"]["assetPermissions"] == "locked_not_nested"
    assert admin.delete("/api/projects/finance", headers=CSRF).status_code == 204
    assert admin.delete("/api/projects/sales", headers=CSRF).status_code == 409  # still holds dashboards


def test_site_administration(app: Any) -> None:
    admin, erin = as_user(app, "admin"), as_user(app, "erin")
    assert erin.get("/api/admin/users").status_code == 403
    users = {u["username"]: u for u in admin.get("/api/admin/users").json()}
    assert users["erin"]["siteRole"] == "explorer"
    assert users["erin"]["groups"] == ["Executives"]
    assert (
        admin.post(
            "/api/admin/users", json={"username": "zoe", "siteRole": "creator"}, headers=CSRF
        ).status_code
        == 201
    )
    # A role change signs the user out everywhere.
    assert admin.patch("/api/admin/users/erin", json={"siteRole": "viewer"}, headers=CSRF).status_code == 204
    assert erin.get("/api/me").status_code == 401
    assert admin.patch("/api/admin/users/admin", json={"disabled": True}, headers=CSRF).status_code == 409
    group = {"members": ["zoe", "ivan"], "minimumSiteRole": "explorer"}
    assert admin.put("/api/admin/groups/Analysts", json=group, headers=CSRF).status_code == 204
    groups = {g["name"]: g for g in admin.get("/api/admin/groups").json()}
    assert groups["Analysts"]["members"] == ["ivan", "zoe"]
    users = {u["username"]: u for u in admin.get("/api/admin/users").json()}
    assert users["ivan"]["effectiveSiteRole"] == "explorer"
    assert admin.delete("/api/admin/groups/Analysts", headers=CSRF).status_code == 204
    settings = {"name": "Dev site", "settings": {"athenaWorkgroup": "wg", "maxConcurrentQueries": 5}}
    assert admin.patch("/api/admin/site", json=settings, headers=CSRF).status_code == 204
    assert admin.get("/api/admin/site").json()["settings"] == {
        "athenaWorkgroup": "wg",
        "maxConcurrentQueries": 5,
    }
    bad = admin.patch("/api/admin/site", json={"settings": {"rm -rf": 1}}, headers=CSRF)
    assert bad.status_code == 422
    log = admin.get("/api/admin/audit", params={"action": "user_update"}).json()
    assert log[0]["detail"]["changed"] == ["siteRole"]
    assert erin.get("/api/admin/audit").status_code in (401, 403)


def test_site_file_mode_reads_but_does_not_write(tmp_path: Path) -> None:
    app = create_app(Settings(environment="test", dev_login=True, session_db=tmp_path / "s.db"))
    erin = as_user(app, "erin")
    assert {d["id"] for d in erin.get("/api/content", params={"kind": "dashboard"}).json()} >= {
        "sales-overview"
    }
    assert erin.get("/api/projects").status_code == 200
    assert erin.put("/api/favorites/dashboard/sales-overview", headers=CSRF).status_code == 501
    assert erin.get("/api/home").json()["favorites"] == []
