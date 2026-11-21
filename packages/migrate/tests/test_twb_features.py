"""P2.1 parser features, each checked on a real corpus file (plus synthetic edge cases)."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.fieldref import shelf_refs
from tabfree_migrate.twb import parse_file, tableau_version
from tabfree_schemas import SchemaKind, schema_errors

CORPUS = Path(__file__).resolve().parents[3] / "corpus"
pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not present")

LOGSHARK = "workbooks/tableau__Logshark/LogShark/Workbooks"
NAVIGATOR = "workbooks/tableau__extension-hierarchy-navigator-sandboxed/docs"


@cache
def ir_of(relative: str) -> dict[str, Any]:
    ir = parse_file(CORPUS / relative)
    assert schema_errors(SchemaKind.TABLEAU_IR, ir) == []
    return ir


def sheet(ir: dict[str, Any], name: str) -> dict[str, Any]:
    return next(w for w in ir["worksheets"] if w["name"] == name)


def columns(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for ds in ir["datasources"] for c in ds.get("columns", [])}


def sets(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for ds in ir["datasources"] for s in ds.get("sets", [])}


# --- normalisation and version -----------------------------------------------------------------------


def test_true_feature_flag_variants_win(tmp_path: Path) -> None:
    twb = tmp_path / "flags.twb"
    twb.write_text(
        "<?xml version='1.0'?><workbook source-build='2024.2.0 (20242.24.0613.0835)' version='18.1'>"
        "<datasources><datasource name='federated.1'><connection class='federated'>"
        "<_.fcp.ObjectModelEncapsulateLegacy.false...relation type='table' name='Old' table='[Old]'/>"
        "<_.fcp.ObjectModelEncapsulateLegacy.true...relation type='collection'>"
        "<relation type='table' name='A' table='[A]'/></_.fcp.ObjectModelEncapsulateLegacy.true...relation>"
        "</connection></datasource></datasources><dashboards><dashboard name='D'><zones>"
        "<zone _.fcp.SetMembershipControl.false...type='text'"
        " _.fcp.SetMembershipControl.true...type-v2='text'"
        " h='1' w='1' x='0' y='0' id='1'/></zones></dashboard></dashboards></workbook>",
        encoding="utf-8",
    )
    ir = parse_file(twb)
    assert ir["datasources"][0]["relation"]["type"] == "collection"
    assert ir["dashboards"][0]["zones"][0]["type"] == "text"
    assert ir["source"]["tableauVersion"] == "2024.2.0"


def test_object_model_relation_comes_from_the_true_variant() -> None:
    ir = ir_of(f"{LOGSHARK}/Bridge.twbx")
    ds = next(d for d in ir["datasources"] if d.get("objectGraph", {}).get("relationships"))
    assert ds["relation"]["type"] == "collection"
    (relationship,) = ds["objectGraph"]["relationships"]
    assert relationship["clauses"] == [
        {"left": "[JobId]", "op": "=", "right": "[JobId (BridgeJobEnvironment)]"}
    ]


def test_tableau_version_prefers_source_build(tmp_path: Path) -> None:
    from xml.etree.ElementTree import Element

    assert tableau_version(Element("workbook", {"source-build": "10.2.2 (10200.17.0221.1434)"})) == "10.2.2"
    assert (
        tableau_version(Element("workbook", {"source-build": "0.0.0 (0000.0.0.0)", "version": "26.1"}))
        == "2026.1"
    )
    assert tableau_version(Element("workbook", {"version": "18.1"})) is None
    assert tableau_version(Element("workbook", {"version": "9.3"})) == "9.3"


# --- data sources --------------------------------------------------------------------------------------


def test_join_clauses() -> None:
    ir = ir_of(f"{LOGSHARK}/Art.twbx")
    joins = [d["relation"] for d in ir["datasources"] if d.get("relation", {}).get("type") == "join"]
    assert joins[0]["join"] == "inner"
    assert joins[0]["clauses"][0] == {"left": "[Begin].[ArtId]", "op": "=", "right": "[End].[ArtId]"}


def test_extract_incremental_refresh() -> None:
    ir = ir_of(
        "workbooks/tableau__community-tableau-server-insights/datasources/ts_background_tasks/ts_background_tasks_04.02.twb"
    )
    extract = next(d["extract"] for d in ir["datasources"] if "extract" in d)
    assert (extract["engine"], extract["incremental"], extract["incrementalKey"]) == (
        "hyper",
        True,
        "[Job Id]",
    )
    assert [e["type"] for e in extract["refreshEvents"]] == ["create", "increment"]


def test_data_source_filter_excludes_members() -> None:
    ir = ir_of("workbooks/tableau__community-tableau-server-insights/datasources/ts_users/ts_users_01.02.twb")
    (flt,) = [f for d in ir["datasources"] for f in d.get("filters", [])]
    assert (flt["kind"], flt["members"], flt["exclude"]) == ("categorical", ['"_system"'], True)


def test_groups_with_other() -> None:
    clusters = columns(
        ir_of("workbooks/tableau__Visualization-Linting/example-data/World Indicators Mirages.twbx")
    )["[Country (clusters)]"]
    assert clusters["semanticRole"] == "[Country].[ISO3166_2]"
    group = clusters["group"]
    assert (group["source"], group["other"]) == ("[Country]", '"Not Clustered"')
    friendly = next(g for g in group["groups"] if g["name"] == '"Friendly"')
    assert '"Albania"' in friendly["members"]
    manufacturer = columns(ir_of("workbooks/tableau__document-api-python/test/assets/shapes_test.twb"))[
        "[Product Name (group)]"
    ]["group"]
    assert manufacturer["groups"][0] == {
        "name": '"3D Systems"',
        "members": [
            '"3D Systems Cube Printer, 2nd Generation, Magenta"',
            '"3D Systems Cube Printer, 2nd Generation, White"',
        ],
    }


def test_bins() -> None:
    cols = columns(ir_of("workbooks/tableau__document-api-python/test/assets/shapes_test.twb"))
    assert cols["[Profit (bin)]"]["bin"] == {
        "source": "[Profit]",
        "sizeParameter": "[Parameters].[Parameter 2]",
        "peg": 0,
        "decimals": 0,
    }
    prep = columns(ir_of(f"{LOGSHARK}/Prep.twbx"))["[Flow Run Time In Milliseconds (bin)]"]["bin"]
    assert (prep["source"], prep["size"]) == ("[FlowRunTimeInMilliseconds]", 380916)


def test_sets_members_exclude_top_n_and_combined() -> None:
    lasso = sets(ir_of("workbooks/tableau__tab-compare/TabCompare.twbx"))["[Set 1]"]
    assert (lasso["kind"], lasso["caption"], lasso["builder"]) == ("set", "False Positives", "lasso-group")
    assert '"2015AnnualQuotaAttainment/sheets/RegionalAttainment"' in lasso["members"]
    linearity = sets(ir_of("workbooks/tableau__e-bikes-demo/public/data/eBike Sales Analysis.twbx"))[
        "[Linearity Size]"
    ]
    assert (linearity["members"], linearity["exclude"]) == (['"Older"', "%null%"], True)
    top = sets(ir_of(f"{LOGSHARK}/Prep.twbx"))["[Flow Run Time In Milliseconds Set]"]["topN"]
    assert top == {
        "end": "top",
        "countExpression": "[Parameters].[Parameter 1]",
        "countParameter": "[Parameters].[Parameter 1]",
        "units": "records",
        "by": "SUM([FlowRunTimeInMilliseconds])",
        "direction": "DESC",
        "level": "[FlowRunTimeInMilliseconds]",
    }
    combined = sets(ir_of("workbooks/tableau__TabMon/TabMon/Resources/Sample Workbooks/2018.2+/TabMon.twb"))[
        "[Label & Name (Combined)]"
    ]
    assert (combined["kind"], combined["levels"]) == (
        "combined",
        ["[Calculation_993606729030094848]", "[name]"],
    )


def test_hierarchies_folders_parameters() -> None:
    ds = ir_of("datasources/tableau__server-client-python/test/assets/World Indicators.tds")["datasources"][0]
    assert ds["drillPaths"][0] == {
        "name": "Location",
        "fields": ["[Region]", "[Subregion]", "[Country / Region]"],
    }
    new_world = ir_of("datasources/tableau__document-api-python/samples/show-fields/new-world.tds")[
        "datasources"
    ][0]
    business = next(f for f in new_world["folders"] if f["name"] == "Business")
    assert "[Ease of Business]" in business["fields"]
    assert next(c for c in new_world["columns"] if c["name"] == "[Ease of Business]")["folder"] == "Business"
    params = {p["name"]: p for p in ir_of(f"{LOGSHARK}/Art.twbx")["parameters"]}
    flame = params["[TimeComparison (copy)]"]["param"]
    assert flame["domain"] == "list"
    assert flame["memberAliases"]['"CPU Flamechart"'] == "CPU"
    assert flame["value"] == '"Component Flamechart"'


# --- worksheets ------------------------------------------------------------------------------------------


def test_shelf_trees_and_structured_encodings() -> None:
    ir = ir_of(f"{LOGSHARK}/Apache.twbx")
    nested = [
        s for w in ir["worksheets"] for k in ("rows", "cols") if (s := w.get(k)) and s["tree"]["op"] == "nest"
    ]
    assert nested
    assert all(len(s["tree"]["args"]) >= 2 for s in nested)
    for shelf in nested:
        assert shelf["instances"] == [r["ref"] for r in shelf_refs(shelf["tree"])]
        assert all(r.get("derivation") for r in shelf_refs(shelf["tree"]))
    color = next(
        r for w in ir["worksheets"] for p in w["panes"] for r in p.get("encodings", {}).get("color", [])
    )
    assert color["ref"].startswith("[federated.")
    assert "derivation" in color


def test_filters_top_n_relative_date_quantitative_context_hide_condition_wildcard() -> None:
    backgrounder = ir_of(f"{LOGSHARK}/Backgrounder.twbx")
    top = next(f for f in sheet(backgrounder, "Jobs Run by Type")["filters"] if f["kind"] == "top-n")
    assert top["context"] is True
    assert (top["topN"]["countParameter"], top["topN"]["by"]) == (
        "[Parameters].[Parameter 1]",
        "COUNTD([JobId])",
    )
    quantitative = next(
        f
        for f in sheet(backgrounder, "Backgrounder Utilization - Wait Time per Hour")["filters"]
        if f["kind"] == "quantitative"
    )
    assert (quantitative["min"], quantitative["includedValues"]) == ("0", "in-range")
    wildcard = next(
        f for f in sheet(backgrounder, "Extract Task Details  ")["filters"] if f["kind"] == "wildcard"
    )
    assert wildcard["wildcard"] == {"ui-pattern-exclude": "true"}

    apache = ir_of(f"{LOGSHARK}/Apache.twbx")
    top25 = next(
        f for w in apache["worksheets"] for f in w["filters"] if f.get("topN", {}).get("count") == 25
    )
    assert top25["topN"]["by"] == "SUM([Calculation_175358938419699719])"

    relative = next(
        f
        for w in ir_of("workbooks/tableau__VizAlerts/demo/VizAlertsDemo.twb")["worksheets"]
        for f in w["filters"]
        if f["kind"] == "relative-date"
    )
    assert (relative["period"], relative["firstPeriod"], relative["lastPeriod"]) == ("month", 0, 0)

    hidden = next(
        f for w in ir_of(f"{LOGSHARK}/Config.twbx")["worksheets"] for f in w["filters"] if f.get("hide")
    )
    assert (hidden["exclude"], hidden["members"]) == (True, ['"wgserver.authentication.login"'])

    condition = next(f for w in ir_of(f"{LOGSHARK}/Tabadmin.twbx")["worksheets"] for f in w["filters"]
                     if f["kind"] == "condition")  # fmt: skip
    assert condition["condition"] == "COUNT([Command]) > 0"
    assert condition["ref"]["derivation"] == "none"


def test_sorts() -> None:
    apache = ir_of(f"{LOGSHARK}/Apache.twbx")
    sorts = [s for w in apache["worksheets"] for s in w.get("sorts", [])]
    manual = next(s for s in sorts if s["kind"] == "manual" and "Very Fast" in str(s.get("order")))
    assert manual["order"][:2] == ['"Very Very Slow"', '"Very Slow"']
    computed = next(s for s in sorts if s["kind"] == "computed")
    assert computed["direction"] in {"ASC", "DESC"}
    assert computed["using"].startswith("[")
    assert computed["aggregation"]
    shelf = next(s for w in ir_of(f"{LOGSHARK}/Bridge.twbx")["worksheets"] for s in w.get("sorts", [])
                 if s["kind"] == "shelf")  # fmt: skip
    assert (shelf["shelf"], shelf["direction"]) == ("rows", "DESC")


def test_table_calcs() -> None:
    tabmon = ir_of("workbooks/tableau__TabMon/TabMon/Resources/Sample Workbooks/2018.2+/TabMon.twb")
    diff = next(i for i in sheet(tabmon, "Active Sessions")["fieldInstances"] if i.get("tableCalc"))
    assert diff["quickTableCalc"] == "diff"
    assert diff["tableCalc"] == {
        "diffOptions": "Relative",
        "orderingType": "Rows",
        "type": "Difference",
        "address": ["-1"],
    }
    rank = next(
        i["tableCalc"]
        for w in ir_of("workbooks/tableau__VizAlerts/demo/VizAlertsDemo.twb")["worksheets"]
        for i in w.get("fieldInstances", [])
        if i.get("tableCalc", {}).get("rank")
    )
    assert rank["rank"] == {"method": "Competition", "direction": "Descending"}
    world = ir_of("datasources/tableau__server-client-python/test/assets/World Indicators.tds")
    calc = next(
        c["calculation"] for c in columns(world).values() if "tableCalcSettings" in c.get("calculation", {})
    )
    assert calc["tableCalcSettings"] == {"orderingType": "Rows"}
    nested = next(
        i["tableCalc"]
        for w in ir_of(f"{LOGSHARK}/Bridge.twbx")["worksheets"]
        for i in w.get("fieldInstances", [])
        if i.get("tableCalc", {}).get("nested")
    )
    assert nested["orderingType"] == "Table"
    assert nested["nested"][0]["field"].startswith("[federated.")


def test_addressing_fields() -> None:
    ir = ir_of(f"{NAVIGATOR}/Dimensional-Flat example Hierarchy Navigator_v2018.2.twbx")
    calc = next(i["tableCalc"] for w in ir["worksheets"] for i in w.get("fieldInstances", [])
                if i.get("tableCalc", {}).get("addressing"))  # fmt: skip
    assert calc["orderingType"] == "Field"
    assert all(a.startswith("[") for a in calc["addressing"])


def test_totals() -> None:
    ir = ir_of("workbooks/tableau__VizAlerts/demo/VizAlertsDemo.twb")
    totals = next(w["totals"] for w in ir["worksheets"] if w.get("totals", {}).get("subtotals"))
    assert totals["subtotals"][0] == "[dataengine.42019.618651678240].[none:Region:nk]"
    assert any(w.get("totals", {}).get("rows") or w.get("totals", {}).get("cols") for w in ir["worksheets"])


# --- dashboards, actions, stories ----------------------------------------------------------------------


def test_floating_zones_and_device_layouts() -> None:
    ir = ir_of("workbooks/tableau__e-bikes-demo/public/data/eBike Sales Analysis.twbx")
    zones = next(d for d in ir["dashboards"] if d["name"] == "Sales Analysis")["zones"]
    assert zones[0]["type"] == "layout-basic"
    assert "floating" not in zones[0]
    legend = zones[1]
    assert (legend["type"], legend["worksheet"], legend["floating"]) == ("color", "map", True)
    assert all(z.get("floating") for z in zones[1:])
    art = ir_of(f"{LOGSHARK}/Art.twbx")
    layout = next(d for d in art["dashboards"] if d.get("deviceLayouts"))["deviceLayouts"][0]
    assert (layout["device"], layout["name"]) == ("phone", "Phone")


def test_filter_highlight_and_url_actions() -> None:
    apache = ir_of(f"{LOGSHARK}/Apache.twbx")
    actions = {a["name"]: a for a in apache["actions"]}
    select = actions["[Action12]"]
    assert select["type"] == "filter"
    assert select["trigger"] == "select"
    assert select["source"]["worksheets"] == ["Workbooks by % of Error"]
    assert select["target"] == {
        "excludeSheets": ["Workbooks by % of Error"],
        "dashboard": "Workbook Error Percentages and Timeline",
    }
    assert select["fields"] == [
        {
            "source": "[Calculation_175358938396983299]",
            "target": "[federated.0ol3tzd1nl7ehf17w7cax0n3xrz5].[Calculation_175358938396983299]",
        }
    ]
    assert select["clear"] == {"autoClear": True}
    highlight = actions["[Action10]"]
    assert (highlight["type"], highlight["trigger"], highlight["fieldCaptions"]) == (
        "highlight",
        "hover",
        ["Request Errors Legend"],
    )
    urls = [a for a in apache["actions"] if a["type"] == "url"]
    assert urls
    assert all(a["target"]["url"] == a["link"]["expression"] for a in urls)


def test_set_and_parameter_actions() -> None:
    (set_action,) = ir_of("workbooks/tableau__document-api-python/samples/preserve-namespaces/filtering.twb")[
        "actions"
    ]
    assert set_action["type"] == "edit-group"
    assert set_action["target"] == {"set": "[federated.1df63xu0j2dvhd1e3sooz18pbrcc].[BurstoutSet]"}
    assert (set_action["setMode"], set_action["clear"]) == ("assign", {"setOption": "show-all"})
    navigator = ir_of(f"{NAVIGATOR}/Hierarchy Navigator Extension v2.twbx")
    param = next(a for a in navigator["actions"] if a["type"] == "edit-parameter")
    assert param["target"] == {"parameter": "[Parameters].[Employee Name Param (copy)]"}
    assert param["fields"][0]["source"] == "[federated.0i1ez841l53o5y15yy65d1ojyjq4].[none:employeeNumber:nk]"
    assert (param["aggregation"], param["clear"]) == ("attr", {"option": "do-nothing", "value": "i:1"})


def test_go_to_sheet_action(tmp_path: Path) -> None:
    twb = tmp_path / "nav.twb"
    twb.write_text(
        "<?xml version='1.0'?><workbook version='26.1'><worksheets><worksheet name='Detail'/></worksheets>"
        "<actions><nav-action caption='Go' name='[Action1]'><activation type='on-select'/>"
        "<source dashboard='Home' type='sheet' worksheet='Overview'/>"
        "<params><param name='target' value='Detail'/></params></nav-action></actions></workbook>",
        encoding="utf-8",
    )
    (act,) = parse_file(twb)["actions"]
    assert (act["type"], act["target"], act["source"]["worksheets"]) == (
        "sheet-navigate",
        {"worksheets": ["Detail"]},
        ["Overview"],
    )


def test_stories() -> None:
    (story,) = ir_of(f"{NAVIGATOR}/Hierarchy Navigator Extension v2.twbx")["stories"]
    assert story["name"] == "Instructions and Examples"
    assert story["navigation"] == "caption"
    point = next(p for p in story["points"] if p.get("id") == "16")
    assert point["target"] == "Example 1a - Set"
    assert point["state"] == {"worksheets": ["ClassicModels Hierarchy"], "dashboards": ["Example 1a - Set"]}


def test_blend_relationships() -> None:
    blend = ir_of(f"{LOGSHARK}/ClusterController.twbx")["blendRelationships"][0]
    assert blend["source"] == "federated.0mjeagw1xwtgls15h2f4k0e0pbdg"
    assert blend["fields"][0]["target"] == "[federated.0bu0ssr0xwi9sk16asjaz0i5qm7o].[cnt:Timestamp:ok]"
