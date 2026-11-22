from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabfree_compiler import CompileError
from tabfree_compiler.dashboard import bind_domain, bind_query

MANIFEST: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[2] / "schemas/examples/dashboards/sales-overview/tabfree.json"
    ).read_text(encoding="utf-8")
)
STATE: dict[str, Any] = {
    "filters": {
        "region": {"members": ["East", "West"]},
        "order_date": {"relative": {"period": "month", "from": -2, "to": 0}},
    },
    "parameters": {"top_n": 5},
    "highlight": None,
}


def test_filters_and_parameters_bind_to_the_declared_fields() -> None:
    bound = bind_query(MANIFEST, "by_region", STATE)
    assert bound.model == "superstore_sales"
    assert bound.query is MANIFEST["queries"]["by_region"]
    assert list(bound.filters) == [
        {"field": "Region", "kind": "categorical", "members": ["East", "West"]},
        {
            "field": "Order Date",
            "kind": "relative_date",
            "context": True,
            "relative": {"period": "month", "from": -2, "to": 0},
        },
    ]
    assert bound.parameters == {"top_n": 5}


def test_filter_actions_apply_only_to_their_targets() -> None:
    selections = {"by_region": [{"Region": "East"}]}
    trend = bind_query(MANIFEST, "trend", {"filters": {}, "parameters": {}}, selections)
    assert {"field": "Region", "kind": "categorical", "members": ["East"]} in trend.filters
    source = bind_query(MANIFEST, "by_region", {"filters": {}, "parameters": {}}, selections)
    assert source.filters == ()  # the action's source is not one of its targets


@pytest.mark.parametrize(
    ("state", "error"),
    [
        ({"filters": {"nope": {"members": []}}}, "unknown filter"),
        ({"filters": {"region": {"min": 1}}}, "does not fit"),
        ({"filters": {"region": "East"}}, "must be an object"),
        ({"parameters": {"nope": 1}}, "unknown parameter"),
        ({"parameters": {"top_n": "5; DROP"}}, "must be an integer"),
        ({"parameters": {"top_n": 500}}, "above its maximum"),
    ],
)
def test_undeclared_or_malformed_state_is_rejected(state: dict[str, Any], error: str) -> None:
    with pytest.raises(CompileError, match=error):
        bind_query(MANIFEST, "by_region", state)


def test_unknown_queries_and_selection_sources_are_rejected() -> None:
    with pytest.raises(CompileError, match="unknown query"):
        bind_query(MANIFEST, "drop_tables", STATE)
    with pytest.raises(CompileError, match="unknown selection source"):
        bind_query(MANIFEST, "trend", STATE, {"elsewhere": [{"Region": "East"}]})


def test_domain_lists_distinct_members_in_order() -> None:
    bound = bind_domain(MANIFEST, "region", STATE)
    assert bound.query == {
        "dimensions": ["Region"],
        "sort": [{"by": "Region", "dir": "asc"}],
        "limit": 10_000,
    }
    assert bound.filters == ()  # onlyRelevantValues is off
    cascading = {
        **MANIFEST,
        "filters": {
            **MANIFEST["filters"],
            "region": {**MANIFEST["filters"]["region"], "onlyRelevantValues": True},
        },
    }
    assert [f["field"] for f in bind_domain(cascading, "region", STATE).filters] == ["Order Date"]
