"""P3.1/P3.2: the metadata database — migrations, the imported site, revisions, sessions, PATs,
local accounts and the audit log. Each test runs on SQLite, and on Postgres when
``TABFREE_TEST_POSTGRES_URL`` points at an empty database (CI's service container, or a local one).
"""

from __future__ import annotations

import datetime as dt
import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text, update

from tabfree_api.authz import Capability, Principal, Rule, SiteRole
from tabfree_api.content import ContentError, Site
from tabfree_api.db import connect, downgrade, metadata, upgrade
from tabfree_api.db import schema as t
from tabfree_api.db.seed import create_site, import_site
from tabfree_api.db.store import PAT_IDLE_DAYS, AuditLog, DbSessionStore, DbSite, LocalAccounts
from tabfree_api.settings import Settings

POSTGRES = os.environ.get("TABFREE_TEST_POSTGRES_URL")
DEV_SITE = Settings().site_file


def _reset_postgres(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = connect(f"sqlite:///{tmp_path / 'tabfree.db'}")
    else:
        if not POSTGRES:
            pytest.skip("set TABFREE_TEST_POSTGRES_URL to run against Postgres")
        eng = connect(POSTGRES)
        _reset_postgres(eng)
    upgrade(eng)
    yield eng
    if request.param == "postgres":
        _reset_postgres(eng)
    eng.dispose()


@pytest.fixture
def site(engine: Engine) -> DbSite:
    return import_site(engine, Site(DEV_SITE))


def test_migrations_match_the_schema_and_roll_back(engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []
    downgrade(engine, "base")
    with engine.connect() as conn:
        names = set(conn.dialect.get_table_names(conn)) - {"alembic_version"}
    assert names == set()
    upgrade(engine)


def test_imported_site_decides_exactly_like_the_site_file(site: DbSite) -> None:
    """Every dev user, dashboard and capability: the database gives the file's answers."""
    source = Site(DEV_SITE)
    assert set(site.dashboards) == set(source.dashboards)
    assert set(site.models) == set(source.models)
    assert set(site.users) == set(source.users)
    for user, capability in itertools.product(source.users.values(), Capability):
        principal = Principal(user.username, user.groups, user.site_role)
        for dashboard in source.dashboards:
            assert site.can(principal, capability, dashboard) == source.can(
                principal, capability, dashboard
            ), (
                user.username,
                capability,
                dashboard,
            )
        for model in source.models:
            assert site.can_model(principal, capability, model) == source.can_model(
                principal, capability, model
            )
        assert site.visible(principal) == source.visible(principal)
    assert site.bundle("sales-overview").manifest == source.bundle("sales-overview").manifest
    assert site.data_version("superstore_sales") == source.data_version("superstore_sales")
    assert site.query == source.query
    assert site.models["superstore_sales"]["id"] == "superstore_sales"


def test_revisions_are_immutable_and_restorable(site: DbSite, engine: Engine) -> None:
    first = site.revision("dashboard", "sales-overview")
    manifest = {**first.document, "title": "Sales overview v2"}
    assert site.publish("dashboard", "sales-overview", manifest, files=first.files, by="admin") == 2
    # Identical content publishes no new revision.
    assert site.publish("dashboard", "sales-overview", manifest, files=first.files, by="admin") == 2
    assert site.bundle("sales-overview").manifest["title"] == "Sales overview v2"
    assert site.restore("dashboard", "sales-overview", 1, by="admin") == 3
    history = site.revisions("dashboard", "sales-overview")
    assert [r.number for r in history] == [1, 2, 3]
    assert history[1].created_by == "admin"
    assert history[2].sha256 == history[0].sha256
    if engine.dialect.name == "postgresql":
        with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
            conn.execute(update(t.content_revisions).values(message="rewritten"))


def test_new_content_and_nested_projects_copy_default_rules(site: DbSite) -> None:
    exec_ = Principal("erin", frozenset({"Executives"}), SiteRole.EXPLORER)
    site.create_project("forecasts", "Forecasts", parent="migrated", owner="admin")
    assert site.projects["forecasts"].parent == "migrated"
    # The child inherited the parent's dashboard defaults when it was created.
    assert site.projects["forecasts"].defaults["dashboard"] == site.projects["migrated"].defaults["dashboard"]
    site.publish(
        "dashboard", "q3", {"title": "Q3"}, files={"index.html": "<p>"}, project="forecasts", by="admin"
    )
    assert site.can(exec_, Capability.VIEW, "q3")
    # Customizable project: the dashboard's own rules can differ from the defaults afterwards.
    site.set_rules("dashboard", "q3", [Rule(None, "Executives", {Capability.VIEW: "deny"})])
    assert not site.can(exec_, Capability.VIEW, "q3")
    site.move("dashboard", "q3", "sales")  # Sales is locked: its defaults decide
    assert not site.can(Principal("x", frozenset({"Contractors"}), SiteRole.VIEWER), Capability.VIEW, "q3")
    assert site.can(exec_, Capability.VIEW, "q3")
    with pytest.raises(ContentError, match="parent"):
        site.create_project("orphan", "Orphan", parent="nope")
    site.soft_delete("dashboard", "q3")
    assert "q3" not in site.dashboards


def test_group_minimum_role_and_sign_in_resolution(site: DbSite) -> None:
    site.ensure_group("Analysts", minimum_site_role=SiteRole.CREATOR)
    fullname, groups, role = site.resolve("ivan", "", frozenset({"Analysts"}))
    assert role is SiteRole.CREATOR
    assert groups == {"Analysts"}
    assert fullname == "Ivan (no groups)"
    fullname, groups, role = site.resolve("newcomer", "New Comer", frozenset())
    assert (fullname, role) == ("New Comer", SiteRole.VIEWER)  # the site's default role
    assert "newcomer" in site.users


def test_sessions_and_pats_carry_email_and_attributes(site: DbSite, engine: Engine) -> None:
    site.ensure_user("alice", email="alice@example.com", attributes={"region": "East"})
    store = DbSessionStore(engine, site.id)
    token = store.create(
        "alice", "Alice", frozenset(), SiteRole.VIEWER, 60, email="a@x.test", attributes={"region": "West"}
    )
    info = store.get(token)
    assert info is not None
    assert (info.email, dict(info.attributes)) == ("a@x.test", {"region": "West"})
    assert info.principal().attributes == {"region": "West"}
    pat = store.get(store.create_pat("alice", "rls"))
    assert pat is not None
    assert (pat.email, dict(pat.attributes)) == ("alice@example.com", {"region": "East"})


def test_sessions_and_flows(site: DbSite, engine: Engine) -> None:
    store = DbSessionStore(engine, site.id)
    token = store.create("alice", "Alice", frozenset({"Managers"}), SiteRole.VIEWER, 3600)
    info = store.get(token)
    assert info is not None
    assert info.groups == {"Managers"}
    assert info.site_role is SiteRole.VIEWER
    assert store.get(token + "x") is None
    store.delete_user_sessions("alice")
    assert store.get(token) is None
    expired = store.create("alice", "Alice", frozenset(), SiteRole.VIEWER, -1)
    assert store.get(expired) is None
    state, nonce, verifier = store.start_flow("/x")
    assert store.finish_flow(state) == (nonce, verifier, "/x")
    assert store.finish_flow(state) is None  # single use


def test_personal_access_tokens(site: DbSite, engine: Engine) -> None:
    store = DbSessionStore(engine, site.id)
    token = store.create_pat("bob", "ci")
    with pytest.raises(ContentError, match="exists"):
        store.create_pat("bob", "ci")
    info = store.get(token)
    assert info is not None
    assert info.username == "bob"
    assert info.groups == {"Managers"}
    [listed] = store.list_pats("bob")
    assert listed["name"] == "ci"
    assert listed["lastUsedAt"] is not None
    assert store.get(token.replace(".", ".x")) is None
    # Unused for more than 15 days: no longer valid.
    with engine.begin() as conn:
        stale = dt.datetime.now(dt.UTC) - dt.timedelta(days=PAT_IDLE_DAYS + 1)
        conn.execute(update(t.personal_access_tokens).values(last_used_at=stale))
    assert store.get(token) is None
    other = store.create_pat("bob", "laptop", days=30)
    assert store.revoke_pat("bob", listed["id"])
    assert not store.revoke_pat("alice", other.split(".")[0][4:])  # not alice's token
    assert store.get(other) is not None


def test_local_accounts(site: DbSite, engine: Engine) -> None:
    accounts = LocalAccounts(engine)
    with pytest.raises(ContentError, match="12 characters"):
        accounts.set_password("admin", "short")
    accounts.set_password("admin", "correct horse battery")
    assert accounts.verify("admin", "correct horse battery")
    assert not accounts.verify("admin", "wrong password here")
    assert not accounts.verify("nobody", "correct horse battery")
    assert not accounts.verify("alice", "anything at all")  # an identity-provider account
    for _ in range(LocalAccounts.MAX_FAILURES):
        accounts.verify("admin", "guess guess guess")
    assert not accounts.verify("admin", "correct horse battery")  # locked out


def test_audit_log(site: DbSite, engine: Engine) -> None:
    audit = AuditLog(engine, site.id)
    audit.record("view", actor="alice", resource_kind="dashboard", resource_id="sales-overview")
    audit.record("view", actor="mallory", outcome="deny", resource_kind="dashboard", resource_id="x")
    events = audit.events()
    assert [e["actor"] for e in events] == ["mallory", "alice"]
    assert [e["actor"] for e in audit.events(action="view", actor="alice")] == ["alice"]
    assert len(audit.events(before=events[0]["id"])) == 1
    if engine.dialect.name == "postgresql":
        with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_events"))


def test_create_site_twice_is_refused(engine: Engine) -> None:
    create_site(engine, "s1", "One")
    with pytest.raises(ContentError, match="exists"):
        create_site(engine, "s1", "One")


@pytest.mark.parametrize("flavour", ["sqlite"])
def test_the_app_runs_on_the_database(flavour: str, tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    from fastapi.testclient import TestClient

    from tabfree_api.main import create_app

    url = f"sqlite:///{tmp_path / 'app.db'}"
    eng = connect(url)
    upgrade(eng)
    import_site(eng, Site(DEV_SITE))
    LocalAccounts(eng).set_password("admin", "correct horse battery")
    app = create_app(Settings(environment="test", dev_login=True, database_url=url, site_id="dev"))
    client = TestClient(app)
    response = client.post("/auth/dev", content="user=erin", follow_redirects=False)
    assert response.status_code == 303
    listed = client.get("/api/dashboards").json()
    assert {d["id"] for d in listed} >= {"sales-overview"}
    data = client.post(
        "/api/dashboards/sales-overview/query",
        json={
            "query": next(iter(client.get("/api/dashboards/sales-overview").json()["manifest"]["queries"])),
            "state": {"filters": {}, "parameters": {}},
        },
        headers={"X-TabFree-Request": "1"},
    )
    assert data.status_code == 200, data.text
    # A PAT works without the cookie or the CSRF header.
    created = client.post("/api/tokens", json={"name": "script"}, headers={"X-TabFree-Request": "1"}).json()
    bare = TestClient(app)
    assert (
        bare.get("/api/me", headers={"Authorization": f"Bearer {created['token']}"}).json()["username"]
        == "erin"
    )
    assert bare.get("/api/me").status_code == 401
    # Local sign-in.
    bad = bare.post("/auth/local", content="username=admin&password=nope", follow_redirects=False)
    assert bad.headers["location"] == "/auth/local?error=1"
    good = bare.post(
        "/auth/local", content="username=admin&password=correct+horse+battery", follow_redirects=False
    )
    assert good.status_code == 303
    assert bare.get("/api/me").json()["siteRole"] == "site_administrator"
    actions = [e["action"] for e in AuditLog(eng, "dev").events()]
    assert {"sign_in", "view", "token_create"} <= set(actions)
