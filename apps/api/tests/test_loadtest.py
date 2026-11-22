"""P7.2: the load test harness, run in-process against a small dev site."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.loadtest import DEFAULT_TARGETS, check, percentile, run
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

httpx = pytest.importorskip("httpx2")
pytest.importorskip("duckdb")


def test_percentiles() -> None:
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([1, 2, 3, 4, 100], 0.95) == 100
    assert percentile([], 0.9) == 0.0


def test_a_short_run_opens_dashboards_and_reports_against_targets(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'load.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    app = create_app(
        Settings(
            environment="test", dev_login=True, database_url=url, site_id="dev", base_url="http://testserver"
        )
    )
    tokens = []
    for user in ("erin", "alice", "admin"):
        client = TestClient(app)
        client.post("/auth/dev", content=f"user={user}", follow_redirects=False)
        tokens.append(
            client.post("/api/tokens", json={"name": "load"}, headers={"X-TabFree-Request": "1"}).json()[
                "token"
            ]
        )

    async def go() -> dict[str, Any]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await run(
                client, tokens[:2], users=6, seconds=1.0, burst=4, think_s=0.2, admin_token=tokens[2]
            )

    report = asyncio.run(go())
    assert report["burst_open"]["count"] == 4
    assert report["open"]["count"] >= 1
    assert report["steps"]["query"]["count"] >= 4
    assert report["error_rate"] == 0.0
    assert report["server"]["count"] >= 4
    assert check(report, {**DEFAULT_TARGETS, "cache_hit_rate": 0.0}) == []
    assert check({**report, "error_rate": 0.5}, DEFAULT_TARGETS)[0].startswith("error_rate")
