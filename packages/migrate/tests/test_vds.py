from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from tabfree_migrate.tableau import TableauAccessError
from tabfree_migrate.vds import Field, UrllibTransport, VizqlDataService, compare_calc


class FakeTransport:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, Any], Mapping[str, str]]] = []

    def post_json(self, url: str, body: Mapping[str, Any], headers: Mapping[str, str]) -> Mapping[str, Any]:
        self.calls.append((url, body, headers))
        return self.response


def test_evaluate_sends_a_calculation_and_returns_rows() -> None:
    fake = FakeTransport({"data": [{"Region": "East", "value": 1.5}]})
    vds = VizqlDataService("https://tableau.example.test/", "tok", fake)
    rows = vds.evaluate("ds-luid", "SUM([Sales]) / SUM([Quantity])", by=["Region"])
    assert rows == [{"Region": "East", "value": 1.5}]
    url, body, headers = fake.calls[0]
    assert url == "https://tableau.example.test/api/v1/vizql-data-service/query-datasource"
    assert headers["X-Tableau-Auth"] == "tok"
    assert body["query"]["fields"] == [
        {"fieldCaption": "Region"},
        {"calculation": "SUM([Sales]) / SUM([Quantity])", "fieldAlias": "value"},
    ]


def test_errors_and_validation() -> None:
    with pytest.raises(TableauAccessError, match="no data"):
        VizqlDataService("https://x", "t", FakeTransport({"message": None})).query("d", [Field(caption="A")])
    with pytest.raises(ValueError, match="caption"):
        Field().to_json()
    with pytest.raises(TableauAccessError, match="https"):
        UrllibTransport().post_json("http://insecure", {}, {})
    assert Field(caption="Sales", function="SUM", alias="s").to_json() == {
        "fieldCaption": "Sales",
        "function": "SUM",
        "fieldAlias": "s",
    }


def test_compare_calc() -> None:
    tableau = [{"Region": "East", "value": "1,234.5"}, {"Region": "West", "value": 2}]
    tabfree = [{"Region": "East", "value": 1234.5}, {"Region": "West", "value": 3}]
    assert compare_calc(tableau, tabfree, by=["Region"]) == ["('West',): Tableau 2.0, TabFree 3.0"]
