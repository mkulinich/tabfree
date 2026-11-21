from __future__ import annotations

import pytest

from tabfree_compiler.parity import compare, normalise, parse_number, unpivot_measures

RESULT = {
    "columns": [
        {"name": "Region", "role": "dimension"},
        {"name": "Order Date", "role": "dimension"},
        {"name": "Sales", "role": "measure"},
        {"name": "Orders", "role": "measure"},
    ],
    "rows": [["East", "2024-01-01", 1234.5004, 12], ["West", "2024-01-01", -3.0, 1]],
}


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("$1,234.50", 1234.5),
        ("12.5%", 0.125),
        ("(3)", -3.0),
        ("-7", -7.0),
        ("1e3", 1000.0),
        ("abc", None),
        ("", None),
    ],
)
def test_numbers(text: str, value: float | None) -> None:
    assert parse_number(text) == value


def test_dates_and_nulls_normalise() -> None:
    assert normalise("1/1/2024") == "2024-01-01"
    assert normalise("Null") is None
    assert normalise(3) == 3.0


def test_matching_export_passes() -> None:
    csv = '﻿Region,Order Date,Sales,Orders\nEast,1/1/2024,"$1,234.50",12\nWest,1/1/2024,(3),1\n'
    report = compare(csv, RESULT)
    assert report.ok
    assert report.matched == 2


def test_mismatches_missing_extra_and_unmapped() -> None:
    csv = "Region,Order Date,Sales,Orders\nEast,1/1/2024,1234.5,13\nNorth,1/1/2024,1,1\n"
    report = compare(csv, RESULT)
    assert report.mismatches == [
        "('East', '2024-01-01') Orders: Tableau '13', TabFree 12"
    ]  # counts are exact
    assert report.missing == [("North", "2024-01-01")]
    assert report.extra == [("West", "2024-01-01")]
    assert not compare("Territory,Sales\nEast,1\n", RESULT).ok


def test_measure_names_long_form_and_renames() -> None:
    rows = [
        {"Area": "East", "Order Date": "2024-01-01", "Measure Names": "Sales", "Measure Values": "1234.5"},
        {"Area": "East", "Order Date": "2024-01-01", "Measure Names": "Orders", "Measure Values": "12"},
    ]
    assert unpivot_measures(rows) == [
        {"Area": "East", "Order Date": "2024-01-01", "Sales": "1234.5", "Orders": "12"}
    ]
    result = {**RESULT, "rows": RESULT["rows"][:1]}
    assert compare(rows, result, mapping={"Area": "Region"}).ok
