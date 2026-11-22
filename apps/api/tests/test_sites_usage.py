"""P6.9 several sites in one server; P6.7 usage exported to the lake and the built-in usage dashboard."""

from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import create_site, import_site
from tabfree_api.engines import DuckDBEngine
from tabfree_api.jobs.queue import JobQueue
from tabfree_api.jobs.schedule import ScheduleError, validate
from tabfree_api.jobs.scheduler import Scheduler, SchedulesStore
from tabfree_api.jobs.worker import Worker
from tabfree_api.main import create_app
from tabfree_api.multisite import SiteDispatcher
from tabfree_api.service import QueryService
from tabfree_api.settings import Settings
from tabfree_api.sites import builtin, install_usage
from tabfree_api.usage_lake import export_day, key, load_into_duckdb, writer_for
from tabfree_compiler.sql import Session

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}


@pytest.fixture
def url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'sites.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    other = create_site(engine, "ops", "Operations")
    other.ensure_user("olga", "Olga", None)
    from tabfree_api.authz import SiteRole

    other.set_user("olga", site_role=SiteRole.SITE_ADMINISTRATOR)
    return url


def settings(url: str, **extra: Any) -> Settings:
    return Settings(
        environment="test", dev_login=True, database_url=url, site_id="dev", permission_cache_s=0,
        base_url="http://testserver", **extra,
    )  # fmt: skip


def test_each_host_gets_its_own_site_and_sessions_do_not_cross(url: str) -> None:
    dispatcher = SiteDispatcher(settings(url, site_hosts={"ops.example": "ops"}))
    dev = TestClient(dispatcher)
    ops = TestClient(dispatcher, base_url="http://ops.example")
    assert dev.post("/auth/dev", content="user=admin", follow_redirects=False).status_code == 303
    assert dev.get("/api/site").json()["name"] == "dev"
    token = dev.cookies["tabfree_session"]
    # The dev site's session means nothing on the ops site, even sent explicitly.
    assert ops.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert ops.post("/auth/dev", content="user=olga", follow_redirects=False).status_code == 303
    assert ops.get("/api/site").json()["name"] == "Operations"
    assert "sales-overview" not in json.dumps(ops.get("/api/content").json())  # dev's content stays on dev
    assert dispatcher.apps["ops"].state.context.settings.base_url == "http://ops.example"


def test_usage_is_exported_and_the_builtin_dashboard_reads_it(url: str, tmp_path: Path) -> None:
    app = create_app(settings(url))
    erin = TestClient(app)
    erin.post("/auth/dev", content="user=erin", follow_redirects=False)
    erin.get("/api/dashboards/sales-overview")
    erin.post("/api/dashboards/sales-overview/query", json={"query": "by_region", "state": {}}, headers=CSRF)
    ctx = app.state.context
    today = dt.datetime.now(dt.UTC).date()
    lake = tmp_path / "lake"
    count = export_day(ctx.site.engine, "dev", today, writer_for(str(lake)))
    assert count >= 2
    lines = gzip.decompress((lake / key("dev", today)).read_bytes()).decode().splitlines()
    events = [json.loads(line) for line in lines]
    assert {"view", "query_metrics"} <= {e["action"] for e in events}
    assert all("East" not in line for line in lines)  # no data values, only who, what and how long
    engine = DuckDBEngine.in_memory()
    load_into_duckdb(engine.connection, lake)
    model = yaml.safe_load(builtin("tabfree_usage.model.yaml"))
    manifest = json.loads(builtin("site-usage/tabfree.json"))
    service = QueryService({"tabfree_usage": model}, lambda _m: engine)
    session = Session(username="admin", today=today)
    kpis = service.query(manifest, "kpis", {}, {}, session)
    values = dict(zip([c["name"] for c in kpis["columns"]], kpis["rows"][0], strict=True))
    assert values["Views"] == 1
    assert values["People"] == 1
    assert values["Queries"] >= 1
    top = service.query(manifest, "top_content", {}, {}, session)
    assert top["rows"][0][:2] == ["sales-overview", 1]


def test_install_usage_is_for_administrators_and_schedules_the_export(url: str, tmp_path: Path) -> None:
    app = create_app(settings(url, usage_lake=str(tmp_path / "lake")))
    ctx = app.state.context
    out = install_usage(ctx.site, by="admin")
    assert out == {"model": 1, "dashboard": 1}
    admin, erin = TestClient(app), TestClient(app)
    admin.post("/auth/dev", content="user=admin", follow_redirects=False)
    erin.post("/auth/dev", content="user=erin", follow_redirects=False)
    assert admin.get("/api/dashboards/site-usage").status_code == 200
    assert erin.get("/api/dashboards/site-usage").status_code == 404
    store = SchedulesStore(ctx.site.engine, "dev", clock=lambda: dt.datetime.now(dt.UTC))
    (schedule,) = [s for s in store.list() if s["name"] == "Usage export"]
    assert schedule["spec"]["jobs"] == ["usage_export"]
    clock = [dt.datetime.now(dt.UTC) + dt.timedelta(days=1, hours=1)]
    queue = JobQueue(ctx.site.engine, clock=lambda: clock[0])
    assert Scheduler(ctx.site.engine, queue, clock=lambda: clock[0]).tick() == 1
    Worker(ctx, queue).drain()
    (job,) = queue.list("dev", kind="usage_export")
    assert job["status"] == "succeeded", job["error"]
    assert (tmp_path / "lake" / key("dev", dt.date.fromisoformat(job["result"]["day"]))).exists()
    with pytest.raises(ScheduleError):
        validate({"frequency": "daily", "jobs": ["rm -rf"]})
