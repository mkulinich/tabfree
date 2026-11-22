"""P6.11: set actions choose a set's members; only sets a set action names may change."""

from __future__ import annotations

from typing import Any

import pytest

from tabfree_api.engines import DuckDBEngine
from tabfree_api.sample import load_sample_data
from tabfree_api.service import QueryService
from tabfree_api.settings import REPO_ROOT
from tabfree_compiler import CompileError
from tabfree_compiler.dashboard import bind_query
from tabfree_compiler.sql import Session
from tabfree_schemas.validate import SchemaKind, load_document, schema_errors

pytest.importorskip("duckdb")

MANIFEST: dict[str, Any] = {
    "schema": "tabfree/dashboard@0",
    "id": "sets",
    "title": "Sets",
    "models": {"m": "superstore_sales"},
    "parameters": {"top_n": {"type": "integer", "default": 3}},
    "queries": {
        "customers": {"dimensions": ["Customer Name"], "measures": ["Sales"]},
        "in_out": {"dimensions": ["Top Customers"], "measures": ["Sales"]},
    },
    "actions": [{"type": "set", "source": ["customers"], "set": "Top Customers", "field": "Customer Name"}],
}


def test_the_manifest_with_a_set_action_is_valid() -> None:
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, MANIFEST) == []


def test_only_sets_named_by_a_set_action_can_change() -> None:
    bound = bind_query(MANIFEST, "in_out", {"sets": {"Top Customers": ["A"]}}, {})
    assert bound.sets == {"Top Customers": ("A",)}
    with pytest.raises(CompileError, match="no set action"):
        bind_query(MANIFEST, "in_out", {"sets": {"Other": ["A"]}}, {})
    with pytest.raises(CompileError, match="plain values"):
        bind_query(MANIFEST, "in_out", {"sets": {"Top Customers": [{"sql": 1}]}}, {})


def test_chosen_members_replace_the_sets_definition() -> None:
    model = dict(
        load_document(
            REPO_ROOT / "packages" / "schemas" / "examples" / "models" / "superstore_sales.model.yaml"
        )
    )
    engine = DuckDBEngine.in_memory()
    load_sample_data(engine.connection)
    service = QueryService({"superstore_sales": model}, lambda _m: engine)
    session = Session(username="erin", groups=frozenset({"Executives"}))
    customers = service.query(MANIFEST, "customers", {}, {}, session)["rows"]
    name, sales = customers[0][0], customers[0][1]
    chosen = service.query(MANIFEST, "in_out", {"sets": {"Top Customers": [name]}}, {}, session)
    inside = {row[0]: row[1] for row in chosen["rows"]}
    assert inside[True] == pytest.approx(sales)
    total = sum(row[1] for row in customers)
    assert inside[False] == pytest.approx(total - sales)
    empty = service.query(MANIFEST, "in_out", {"sets": {"Top Customers": []}}, {}, session)
    assert {row[0] for row in empty["rows"]} == {False}
