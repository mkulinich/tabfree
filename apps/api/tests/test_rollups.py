"""P6.10: rollups answer queries that fit them, with the same results and the same row-level security."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tabfree_api.engines import DuckDBEngine
from tabfree_api.rollups import build_query, choose, derived_model, fits, without_rls
from tabfree_api.sample import load_sample_data
from tabfree_api.service import QueryService
from tabfree_api.settings import REPO_ROOT
from tabfree_compiler import compile_query
from tabfree_compiler.sql import Session
from tabfree_schemas.validate import SchemaKind, schema_errors

pytest.importorskip("duckdb")
ROLLUP = {
    "id": "by_region",
    "table": "orders_by_region",
    "dimensions": ["Region", "Regional Manager", "Category", "Order Date"],
    "measures": ["Sales", "Profit"],
}


@pytest.fixture
def model() -> dict[str, Any]:
    path = REPO_ROOT / "packages" / "schemas" / "examples" / "models" / "superstore_sales.model.yaml"
    from tabfree_schemas.validate import load_document

    doc = copy.deepcopy(dict(load_document(path)))
    doc["rollups"] = [ROLLUP]
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, doc) == []
    return doc


def test_only_queries_that_cannot_change_are_routed(model: dict[str, Any]) -> None:
    derived = derived_model(model, ROLLUP)
    assert derived is not None
    names = {f["name"] for f in derived["fields"]}
    assert {"Region", "Sales", "Profit", "Profit Ratio"} <= names  # the ratio of two sums re-aggregates
    assert "Discount" not in names
    assert "Customer Lifetime Sales" not in names
    assert fits({"dimensions": ["Region"], "measures": ["Sales", "Profit Ratio"]}, [], derived)
    assert fits(
        {
            "dimensions": [{"field": "Order Date", "dateTrunc": "month", "as": "Month"}],
            "measures": [{"field": "Sales", "agg": "sum", "as": "S"}],
        },
        [],
        derived,
    )
    assert fits(
        {"dimensions": ["Region"], "measures": ["Sales"]},
        [{"field": "Category", "kind": "categorical", "members": ["Technology"]}],
        derived,
    )
    assert not fits({"dimensions": ["Segment"], "measures": ["Sales"]}, [], derived)
    assert not fits(
        {"dimensions": ["Region"], "measures": [{"field": "Sales", "agg": "avg", "as": "A"}]}, [], derived
    )
    assert not fits(
        {"dimensions": ["Region"], "measures": ["Sales"]},
        [{"field": "Segment", "kind": "categorical", "members": ["x"]}],
        derived,
    )
    assert not fits(
        {"dimensions": [{"expr": "UPPER([Region])", "as": "R"}], "measures": ["Sales"]}, [], derived
    )
    assert choose(model, {"dimensions": ["State"], "measures": ["Sales"]}, []) is None


def test_rls_is_carried_or_the_rollup_is_not_used(model: dict[str, Any]) -> None:
    policies = model.get("rls") or []
    if policies:
        # The example's policies filter on Region (a rollup dimension): they move to the rollup.
        derived = derived_model(model, ROLLUP)
        assert derived is not None
        assert derived["rls"] == policies
    secured = {**model, "rls": [{"id": "by_state", "condition": "[State] = USERATTRIBUTE('state')"}]}
    assert derived_model(secured, ROLLUP) is None  # State is not in the rollup: never used


def test_rollup_results_match_the_base_tables(model: dict[str, Any]) -> None:
    engine = DuckDBEngine.in_memory()
    load_sample_data(engine.connection)
    build = compile_query(without_rls(model), build_query(model, ROLLUP), dialect="duckdb", session=Session())
    engine.connection.execute(f"CREATE TABLE tabfree_lake.{ROLLUP['table']} AS {build.sql}")
    service = QueryService({model["id"]: model}, lambda _m: engine)
    manifest = {
        "schema": "tabfree/dashboard@0",
        "id": "t",
        "title": "t",
        "models": {"m": model["id"]},
        "queries": {
            "q": {"dimensions": ["Region"], "measures": ["Sales", "Profit Ratio"], "sort": [{"by": "Region"}]}
        },
    }
    session = Session(username="erin", groups=frozenset({"Executives"}))
    fast = service.query(manifest, "q", {}, {}, session)
    assert service.stats.rollup_hits == 1
    service.rollups = False
    slow = service.query(manifest, "q", {}, {}, session)
    assert service.stats.rollup_hits == 1  # the second query read the base tables
    assert [r[0] for r in fast["rows"]] == [r[0] for r in slow["rows"]]
    for a, b in zip(fast["rows"], slow["rows"], strict=True):
        assert a[1] == pytest.approx(b[1])
        assert a[2] == pytest.approx(b[2])


def test_a_manager_sees_the_same_rows_from_the_rollup(model: dict[str, Any]) -> None:
    engine = DuckDBEngine.in_memory()
    load_sample_data(engine.connection)
    build = compile_query(without_rls(model), build_query(model, ROLLUP), dialect="duckdb", session=Session())
    engine.connection.execute(f"CREATE TABLE tabfree_lake.{ROLLUP['table']} AS {build.sql}")
    service = QueryService({model["id"]: model}, lambda _m: engine)
    manifest = {
        "schema": "tabfree/dashboard@0",
        "id": "t",
        "title": "t",
        "models": {"m": model["id"]},
        "queries": {"q": {"dimensions": ["Region"], "measures": ["Sales"]}},
    }
    alice = Session(username="alice", groups=frozenset({"Managers"}))
    fast = service.query(manifest, "q", {}, {}, alice)
    service.rollups = False
    slow = service.query(manifest, "q", {}, {}, alice)
    assert service.stats.rollup_hits == 1
    assert [r[0] for r in fast["rows"]] == [r[0] for r in slow["rows"]] == ["East"]
    assert fast["rows"][0][1] == pytest.approx(slow["rows"][0][1])
