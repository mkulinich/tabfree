from __future__ import annotations

import json
from pathlib import Path

from tabfree_migrate.extract_settings import (
    glue_parameters,
    match_settings,
    resolve_field,
    settings_from_datasource,
    settings_from_ir,
    table_definition,
)
from tabfree_migrate.hyper import ColumnInfo
from tabfree_migrate.twb import parse_file

COLUMNS = [
    ColumnInfo("Order Date", "order_date", "DATE", "date", True),
    ColumnInfo("Region", "region", "TEXT", "string", True, collation="en_US_CI"),
    ColumnInfo("Location", "location", "GEOGRAPHY", "string", True),
    ColumnInfo("Logged", "logged", "TIMESTAMP_TZ", "timestamp", True),
]

DATASOURCE = {
    "name": "federated.1",
    "caption": "Orders",
    "extract": {
        "enabled": True,
        "file": "Data/Extracts/federated_1.hyper",
        "refresh": {"increment-key": "[Order Date]", "incremental-updates": "true"},
        "incrementalKey": "[Order Date]",
        "filters": [{"class": "categorical", "column": "[Region]"}],
        "aggregated": True,
        "raw": {"count": "-1", "units": "records", "rollup-dates": "month"},
        "futureKey": {"x": 1},
    },
}


def test_settings_read_every_known_key_and_keep_the_rest() -> None:
    settings = settings_from_datasource(DATASOURCE)
    assert settings is not None
    assert settings.datasource == "Orders"
    assert settings.incremental
    assert settings.incremental_key == "[Order Date]"
    assert settings.filters == [{"class": "categorical", "column": "[Region]"}]
    assert settings.aggregated is True
    assert settings.aggregation == {"rollup-dates": "month"}
    assert settings.sample == {"count": "-1", "units": "records"}
    assert settings.other == {"futureKey": {"x": 1}}


def test_missing_and_malformed_keys_are_tolerated() -> None:
    assert settings_from_datasource({"name": "x"}) is None
    assert settings_from_ir({}) == []
    assert settings_from_ir({"datasources": "nope"}) == []
    odd = settings_from_datasource({"extract": {"refresh": "bad", "filters": "bad", "raw": None}})
    assert odd is not None
    assert (odd.incremental, odd.filters, odd.aggregated, odd.refresh) == (False, [], None, {})
    flag_only = settings_from_datasource({"extract": {"refresh": {"incremental-updates": "true"}}})
    assert flag_only is not None
    assert flag_only.incremental
    assert flag_only.incremental_key is None


def test_match_settings_by_member_name() -> None:
    a = settings_from_datasource(DATASOURCE)
    b = settings_from_datasource({"extract": {"file": "Data\\Extracts\\other.hyper"}})
    assert a is not None
    assert b is not None
    assert match_settings([a, b], "Data/Extracts/other.hyper") is b
    assert match_settings([a, b], "Data/Extracts/FEDERATED_1.hyper") is a
    assert match_settings([a, b], "unknown.hyper") is None
    lone = settings_from_datasource({"extract": {"enabled": True}})
    assert lone is not None
    assert match_settings([lone], "whatever.hyper") is lone


def test_resolve_field() -> None:
    assert resolve_field("[Order Date]", COLUMNS) is COLUMNS[0]
    assert resolve_field("[federated.1].[order date]", COLUMNS) is COLUMNS[0]
    assert resolve_field("region", COLUMNS) is COLUMNS[1]
    assert resolve_field("[Nope]", COLUMNS) is None


def test_definition_and_glue_parameters_record_the_recipe() -> None:
    settings = settings_from_datasource(DATASOURCE)
    definition = table_definition(settings, COLUMNS, source={"sha256": "ab" * 32, "table": "Extract"})
    assert definition["incremental"]["glue_column"] == "order_date"
    assert definition["filters"] == {"applied_in_data": True, "tableau": DATASOURCE["extract"]["filters"]}
    assert definition["geography"] == {"location": {"encoding": "WKT", "srid": 4326}}
    assert definition["time_zones"] == {"logged": "UTC"}
    assert definition["collations"] == {"region": "en_US_CI"}
    assert definition["warnings"] == []
    params = glue_parameters(definition)
    assert params["tabfree.extract.incremental_key"] == "order_date"
    assert params["tabfree.extract.incremental_field"] == "[Order Date]"
    assert params["tabfree.extract.aggregated"] == "true"
    assert params["tabfree.geo.location.srid"] == "4326"
    assert params["tabfree.tz.logged"] == "UTC"
    assert params["tabfree.collation.region"] == "en_US_CI"
    assert params["tabfree.source.sha256"] == "ab" * 32
    assert json.loads(params["tabfree.definition"])["incremental"]["column"] == "Order Date"
    assert all(isinstance(v, str) for v in params.values())


def test_unknown_key_and_no_settings_are_warnings() -> None:
    settings = settings_from_datasource({"extract": {"incrementalKey": "[Missing]"}})
    definition = table_definition(settings, COLUMNS, source={})
    assert definition["incremental"]["glue_column"] is None
    assert any("Missing" in w for w in definition["warnings"])
    bare = table_definition(None, COLUMNS, source={})
    assert bare["settings_found"] is False
    assert bare["warnings"]
    assert "tabfree.extract.aggregated" not in glue_parameters(bare)


def test_settings_from_a_parsed_workbook(tmp_path: Path) -> None:
    twb = tmp_path / "inc.twb"
    twb.write_text(
        "<?xml version='1.0'?><workbook version='18.1'><datasources>"
        "<datasource name='federated.1' caption='Orders'>"
        "<connection class='federated'/>"
        "<extract enabled='true'><connection class='hyper' dbname='Data/Extracts/orders.hyper'/>"
        "<refresh increment-key='[Order Date]' incremental-updates='true'/>"
        "<filter class='categorical' column='[Region]'/></extract>"
        "</datasource></datasources></workbook>",
        encoding="utf-8",
    )
    found = settings_from_ir(parse_file(twb))
    assert len(found) == 1
    assert found[0].file == "Data/Extracts/orders.hyper"
    assert found[0].incremental_key == "[Order Date]"
    assert found[0].filters
    assert found[0].filters[0]["field"] == "[Region]"  # IR @1 names the filtered field "field"
