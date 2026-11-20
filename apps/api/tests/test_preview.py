from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.main import create_app
from tabfree_api.preview import MANAGERS, sample_rows
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")

STATE: dict[str, Any] = {"filters": {}, "parameters": {}, "highlight": None}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Settings(environment="test", preview=True)))


def query(client: TestClient, user: str, name: str, **extra: Any) -> Any:
    body = {"dashboard": "sales-overview", "query": name, "state": STATE, "user": user, **extra}
    return client.post("/preview/api/query", json=body)


def test_preview_is_off_by_default_and_refused_outside_dev() -> None:
    assert TestClient(create_app(Settings(environment="test"))).get("/preview").status_code == 404
    with pytest.raises(RuntimeError, match="no authentication"):
        create_app(Settings(environment="production", preview=True))


def test_page_and_bundle(client: TestClient) -> None:
    assert "View as" in client.get("/preview").text
    assert client.get("/preview/api/dashboards").json() == ["sales-overview"]
    bundle = client.get("/preview/api/dashboards/sales-overview").json()
    assert bundle["manifest"]["id"] == "sales-overview"
    assert "<tf-chart" in bundle["html"]
    assert client.get("/preview/api/dashboards/../../etc").status_code == 404
    assert client.get("/preview/assets/secrets.txt").status_code == 404


def test_queries_return_typed_formatted_columns(client: TestClient) -> None:
    result = query(client, "erin", "by_region").json()
    assert result["columns"] == [
        {"name": "Region", "type": "string", "role": "dimension"},
        {"name": "Sales", "type": "float", "role": "measure", "format": "$#,##0"},
        {"name": "Profit Ratio", "type": "float", "role": "measure", "format": "0.0%"},
    ]
    expected = {r[6] for r in sample_rows()}
    assert {row[0] for row in result["rows"]} == expected
    assert result["truncated"] is False


def test_row_level_security_follows_the_viewer(client: TestClient) -> None:
    for region, manager in MANAGERS.items():
        rows = query(client, manager, "by_region").json()["rows"]
        assert [row[0] for row in rows] == [region]
    total = sum(r[10] for r in sample_rows())
    [[sales, *_]] = query(client, "erin", "kpis").json()["rows"]
    assert sales == pytest.approx(total)


def test_filters_parameters_and_actions_narrow_results(client: TestClient) -> None:
    state = {**STATE, "filters": {"region": {"members": ["East"]}}, "parameters": {"top_n": 3}}
    rows = query(client, "erin", "top_customers", state=state).json()["rows"]
    assert len(rows) == 3
    by_region = query(client, "erin", "trend", selections={"by_region": [{"Region": "West"}]}).json()
    everything = query(client, "erin", "trend").json()
    assert sum(r[1] for r in by_region["rows"]) < sum(r[1] for r in everything["rows"])


def test_bad_requests_are_400_with_a_reason(client: TestClient) -> None:
    response = query(client, "erin", "by_region", state={**STATE, "filters": {"nope": {"members": []}}})
    assert response.status_code == 400
    assert "unknown filter" in response.json()["detail"]
    assert query(client, "mallory", "by_region").status_code == 400
    assert query(client, "erin", "nope").status_code == 400


def test_domain_respects_row_level_security(client: TestClient) -> None:
    body = {"dashboard": "sales-overview", "filter": "region", "state": STATE}
    rows = client.post("/preview/api/domain", json={**body, "user": "alice"}).json()["rows"]
    assert rows == [["East"]]
    assert len(client.post("/preview/api/domain", json={**body, "user": "erin"}).json()["rows"]) == len(
        MANAGERS
    )
