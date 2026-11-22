from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.metadata import (
    ENTITIES,
    ENTITY_NAMES,
    NODE_LIMIT,
    Edge,
    MetadataStore,
    TscMetadataClient,
    harvest,
)
from tabfree_migrate.testing import FakeMetadataClient


def _nodes() -> dict[str, list[dict[str, Any]]]:
    return {
        "workbooksConnection": [
            {
                "id": f"wb{i}",
                "luid": f"wb-luid-{i}",
                "name": f"Workbook {i}",
                "sheets": [{"id": f"sh{i}", "luid": f"sh-luid-{i}", "name": "Sales", "path": "Sales"}],
                "dashboards": [],
                "embeddedDatasources": [{"id": f"eds{i}", "name": "Sample"}],
                "upstreamDatasources": [{"id": "pds1", "luid": "pds-luid-1", "name": "Orders"}],
            }
            for i in range(5)
        ],
        "sheetsConnection": [
            {
                "id": "sh0",
                "luid": "sh-luid-0",
                "name": "Sales",
                "workbook": {"id": "wb0", "luid": "wb-luid-0", "name": "Workbook 0"},
                "upstreamFields": [{"id": "f1", "name": "Sales", "__typename": "ColumnField"}],
                "upstreamDatasources": [],
            }
        ],
        "calculatedFieldsConnection": [
            {
                "id": "cf1",
                "name": "Profit Ratio",
                "formula": "SUM([Profit])/SUM([Sales])",
                "datasource": {"id": "pds1", "name": "Orders", "__typename": "PublishedDatasource"},
                "fields": [{"id": "f1", "name": "Sales"}, {"id": "f2", "name": "Profit"}],
                "downstreamSheets": [{"id": "sh0", "luid": "sh-luid-0", "name": "Sales"}],
            }
        ],
        "customSQLTablesConnection": [
            {
                "id": "csql1",
                "name": "Custom SQL Query",
                "query": "SELECT * FROM orders WHERE region = 'EU'",
                "database": {"id": "db1", "luid": "db-luid-1", "name": "dwh", "connectionType": "redshift"},
                "tables": [{"id": "t1", "name": "orders", "fullName": "[public].[orders]"}],
                "downstreamWorkbooks": [{"id": "wb1", "luid": "wb-luid-1", "name": "Workbook 1"}],
            }
        ],
        "publishedDatasourcesConnection": [
            {
                "id": "pds1",
                "luid": "pds-luid-1",
                "name": "Orders",
                "hasUserReference": True,
                "upstreamTables": [{"id": "t1", "name": "orders"}],
                "upstreamDatabases": [{"id": "db1", "name": "dwh"}],
            },
            {"id": "pds2", "luid": "pds-luid-2", "name": "Targets", "hasUserReference": False},
        ],
        "embeddedDatasourcesConnection": [
            {"id": "eds3", "name": "Sample", "hasUserReference": True, "workbook": {"id": "wb3"}},
        ],
    }


def test_queries_are_read_only_paged_and_reasonably_sized() -> None:
    for spec in ENTITIES:
        query = spec.query()
        assert query.startswith("query ")
        assert "mutation" not in query
        assert "$first: Int, $afterToken: String" in query
        assert "pageInfo { hasNextPage endCursor }" in query
        assert spec.page_size * 20 < NODE_LIMIT
    names = {spec.name for spec in ENTITIES}
    assert {"workbooks", "sheets", "calculatedFields", "customSQLTables", "publishedDatasources"} <= names
    calc = next(s for s in ENTITIES if s.name == "calculatedFields")
    assert "formula" in calc.fields
    assert "hasUserReference" in next(s for s in ENTITIES if s.name == "publishedDatasources").fields


def test_harvest_stores_nodes_edges_formulas_and_rls(tmp_path: Path) -> None:
    client = FakeMetadataClient(_nodes())
    store = MetadataStore(tmp_path / "inventory.db")
    stats = harvest(client, store, page_size=2)
    assert stats.errors == {}
    assert stats.entities["workbooks"].count == 5
    assert stats.entities["workbooks"].pages == 3
    assert stats.entities["workbooks"].total == 5
    assert all(s.complete for s in stats.entities.values())
    workbook_calls = [v for c, v in client.calls if c == "workbooksConnection"]
    assert [v["afterToken"] for v in workbook_calls] == [None, "2", "4"]

    assert store.formulas() == [("Orders", "Profit Ratio", "SUM([Profit])/SUM([Sales])")]
    rls = store.rls_datasources()
    assert [(r["entity"], r["name"]) for r in rls] == [
        ("publishedDatasources", "Orders"),
        ("embeddedDatasources", "Sample"),
    ]
    assert Edge("workbooks", "wb0", "has_sheet", "sheets", "sh0") in store.edges("has_sheet")
    assert Edge("sheets", "sh0", "uses_field", "fields", "f1") in store.edges()
    refs = store.edges("references_field")
    assert [e.dst_id for e in refs] == ["f1", "f2"]
    sql = next(store.objects("customSQLTables"))
    assert sql["query"].startswith("SELECT * FROM orders")
    assert Edge("customSQLTables", "csql1", "reads_table", "databaseTables", "t1") in store.edges()


def test_reharvest_replaces_edges_instead_of_duplicating(tmp_path: Path) -> None:
    nodes = _nodes()
    store = MetadataStore(tmp_path / "m.db")
    harvest(FakeMetadataClient(nodes), store, ["calculatedFields"])
    nodes["calculatedFieldsConnection"][0]["fields"] = [{"id": "f9", "name": "Discount"}]
    harvest(FakeMetadataClient(nodes), store, ["calculatedFields"])
    assert [e.dst_id for e in store.edges("references_field")] == ["f9"]
    assert store.db.execute("SELECT COUNT(*) FROM metadata_objects").fetchone()[0] == 1


def test_node_limit_halves_the_page_size(tmp_path: Path) -> None:
    client = FakeMetadataClient(_nodes(), max_page=3)
    store = MetadataStore(tmp_path / "m.db")
    stats = harvest(client, store, ["workbooks"], page_size=10)
    assert stats.entities["workbooks"].count == 5
    assert stats.entities["workbooks"].page_size == 2
    assert [v["first"] for _, v in client.calls] == [10, 5, 2, 2, 2]


def test_failing_entity_is_recorded_and_others_continue(tmp_path: Path) -> None:
    client = FakeMetadataClient(_nodes(), broken={"sheetsConnection"})
    store = MetadataStore(tmp_path / "m.db")
    stats = harvest(client, store)
    assert stats.errors == {"sheets": "MetadataError: Cannot query field on sheetsConnection"}
    assert stats.entities["calculatedFields"].count == 1
    run = store.db.execute("SELECT finished_at, stats FROM metadata_runs").fetchone()
    assert run[0] is not None
    assert "Cannot query field" in run[1]


def test_resume_continues_from_the_saved_cursor(tmp_path: Path) -> None:
    store = MetadataStore(tmp_path / "m.db")
    client = FakeMetadataClient(_nodes())
    client.fail_after = 2
    first = harvest(client, store, ["workbooks"], page_size=2)
    assert first.entities["workbooks"].count == 4
    assert "connection reset" in str(first.entities["workbooks"].error)

    retry = FakeMetadataClient(_nodes())
    second = harvest(retry, store, ["workbooks", "sheets"], resume=True, page_size=2)
    assert second.errors == {}
    assert [v["afterToken"] for c, v in retry.calls if c == "workbooksConnection"] == ["4"]
    assert len(list(store.objects("workbooks"))) == 5

    third = FakeMetadataClient(_nodes())
    harvest(third, store, ["workbooks", "sheets"], resume=True)
    assert third.calls == []

    fresh = FakeMetadataClient(_nodes())
    harvest(fresh, store, ["workbooks"])
    assert [v["afterToken"] for _, v in fresh.calls] == [None]


def test_partial_errors_with_data_are_kept_as_warnings(tmp_path: Path) -> None:
    class Partial(FakeMetadataClient):
        def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
            result = super().query(query, variables)
            result["errors"] = [{"message": "Permission denied on field"}]
            return result

    stats = harvest(Partial(_nodes()), MetadataStore(tmp_path / "m.db"), ["publishedDatasources"])
    assert stats.entities["publishedDatasources"].count == 2
    assert stats.entities["publishedDatasources"].warnings == ["Permission denied on field"]


def test_unknown_entity_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown metadata entities: flows"):
        harvest(FakeMetadataClient(), MetadataStore(tmp_path / "m.db"), ["flows"])
    assert "workbooks" in ENTITY_NAMES


def test_tsc_client_passes_queries_through_and_refuses_mutations() -> None:
    class Metadata:
        def __init__(self) -> None:
            self.seen: list[tuple[str, dict[str, Any]]] = []

        def query(self, query: str, variables: dict[str, Any]) -> Any:
            self.seen.append((query, variables))
            return {"data": {}}

    class Server:
        metadata = Metadata()

    client = TscMetadataClient("https://t.example.com", "finance", Server())
    assert client.query("query x { a }", {"first": 1}) == {"data": {}}
    assert Server.metadata.seen == [("query x { a }", {"first": 1})]
    with pytest.raises(ValueError, match="only runs queries"):
        client.query("mutation { deleteEverything }", {})
