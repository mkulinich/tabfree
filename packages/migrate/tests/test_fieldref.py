from __future__ import annotations

import pytest

from tabfree_migrate.fieldref import FieldRefError, parse_field_ref, parse_shelf, shelf_refs, split_ref


def test_simple_reference() -> None:
    ref = parse_field_ref("[federated.0a1b].[sum:Sales:qk]")
    assert ref == {
        "ref": "[federated.0a1b].[sum:Sales:qk]",
        "datasource": "federated.0a1b",
        "kind": "qk",
        "derivation": "sum",
        "derivationName": "Sum",
        "aggregated": True,
        "name": "Sales",
        "field": "[Sales]",
    }


def test_field_names_may_contain_colons_and_escaped_brackets() -> None:
    ref = parse_field_ref("[ds].[none:F: GDP (curr $):qk]")
    assert (ref["derivation"], ref["name"], ref["kind"]) == ("none", "F: GDP (curr $)", "qk")
    ref = parse_field_ref("[ds].[none:Sales [EUR]]:nk]")
    assert ref["name"] == "Sales [EUR]"
    assert ref["field"] == "[Sales [EUR]]]"
    assert split_ref("[a]]b].[c]") == ["a]b", "c"]


def test_date_parts_truncations_and_percentiles() -> None:
    assert parse_field_ref("[ds].[tmn:Order Date:ok]")["derivationName"] == "Month-Trunc"
    assert "aggregated" not in parse_field_ref("[ds].[yr:Order Date:ok]")
    ref = parse_field_ref("[ds].[prc95:Latency:qk]")
    assert (ref["derivation"], ref["derivationName"], ref["percentile"], ref["aggregated"]) == (
        "prc95",
        "Percentile",
        95,
        True,
    )


def test_quick_table_calcs_forecasts_and_pill_index() -> None:
    ref = parse_field_ref("[ds].[pcto:cnt:Number of Records:qk:2]")
    assert (ref["tableCalc"], ref["tableCalcType"], ref["derivation"], ref["index"]) == (
        "pcto",
        "PctTotal",
        "cnt",
        2,
    )
    assert ref["name"] == "Number of Records"
    ref = parse_field_ref("[ds].[rank:sum:F: GDP per capita (curr $):qk]")
    assert (ref["tableCalc"], ref["name"]) == ("rank", "F: GDP per capita (curr $)")
    assert parse_field_ref("[ds].[fVal:sum:Sales:qk]")["forecast"] == "fVal"
    assert parse_field_ref("[ds].[usr:Calculation_37:ok:19]")["index"] == 19


def test_object_model_parameters_and_specials() -> None:
    ref = parse_field_ref("[ds].[__tableau_internal_object_id__].[cnt:Orders_ECFC:qk]")
    assert (ref["datasource"], ref["table"], ref["field"]) == (
        "ds",
        "__tableau_internal_object_id__",
        "[__tableau_internal_object_id__].[Orders_ECFC]",
    )
    ref = parse_field_ref("[__tableau_internal_object_id__].[cnt:Migrated Data:qk]")
    assert "datasource" not in ref
    assert ref["table"] == "__tableau_internal_object_id__"
    param = parse_field_ref("[Parameters].[Parameter 1]")
    assert param == {
        "ref": "[Parameters].[Parameter 1]",
        "datasource": "Parameters",
        "parameter": True,
        "name": "Parameter 1",
        "field": "[Parameter 1]",
    }
    assert parse_field_ref("[ds].[:Measure Names]")["special"] == "measure-names"
    assert parse_field_ref("[ds].[Multiple Values]")["special"] == "multiple-values"
    assert "derivation" not in parse_field_ref("[ds].[Calculation_1]")


@pytest.mark.parametrize("text", ["", "Sales", "[ds].[x", "[ds].[x] y", "[a].[b].[c].[d]"])
def test_malformed_references_raise(text: str) -> None:
    with pytest.raises(FieldRefError):
        parse_field_ref(text)


def test_shelf_nest_cross_concat_tree() -> None:
    tree = parse_shelf(
        "(([ds].[none:Region:nk] / [ds].[none:State:nk]) * ([ds].[sum:Sales:qk] + [ds].[sum:Profit:qk]))"
    )
    assert tree["op"] == "cross"
    nest, concat = tree["args"]
    assert nest["op"] == "nest"
    assert [a["ref"]["name"] for a in nest["args"]] == ["Region", "State"]
    assert concat["op"] == "concat"
    assert [a["ref"]["name"] for a in concat["args"]] == ["Sales", "Profit"]
    assert [r["name"] for r in shelf_refs(tree)] == ["Region", "State", "Sales", "Profit"]


def test_same_operator_chains_are_flattened_and_precedence_applies() -> None:
    tree = parse_shelf("([a].[none:x:nk] / ([a].[none:y:nk] / [a].[none:z:nk]))")
    assert tree["op"] == "nest"
    assert len(tree["args"]) == 3
    tree = parse_shelf("[a].[none:x:nk] + [a].[none:y:nk] / [a].[none:z:nk]")
    assert tree["op"] == "concat"
    assert tree["args"][1]["op"] == "nest"
    leaf = parse_shelf("[ds].[__tableau_internal_object_id__].[cnt:T_1:qk]")
    assert leaf["op"] == "field"
    assert leaf["ref"]["table"] == "__tableau_internal_object_id__"


@pytest.mark.parametrize("text", ["", "([a].[b]", "[a].[b] /", "[a].[b] - [a].[c]", "()"])
def test_malformed_shelves_raise(text: str) -> None:
    with pytest.raises(FieldRefError):
        parse_shelf(text)


def test_helpers_are_reexported_for_other_packages() -> None:
    from tabfree_migrate import twb

    assert twb.parse_field_ref is parse_field_ref
    assert twb.parse_shelf is parse_shelf
