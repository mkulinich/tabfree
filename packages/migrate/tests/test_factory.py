"""Phase 5: the migration factory (remap, estate models, manifests, baseline, governance, waves, run)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from tabfree_migrate.blobstore import LocalDirSink, save_json
from tabfree_migrate.cli import app as cli
from tabfree_migrate.factory.baseline import notes_markdown, render, view_for
from tabfree_migrate.factory.governance import DashboardRef, Identity, ModelRef, decode_alert, plan_governance
from tabfree_migrate.factory.manifest import SheetPlan, plan_workbook
from tabfree_migrate.factory.models import SourceDoc, generate_estate
from tabfree_migrate.factory.remap import ExtractCatalogue, RemapError, Rules, leaves, resolve
from tabfree_migrate.factory.run import FactoryConfig, run_factory
from tabfree_migrate.factory.waves import Candidate, WaveSettings, archive, plan_waves
from tabfree_migrate.inventory import InventoryStore, Item
from tabfree_migrate.twb import parse_file
from tabfree_migrate.worksheets import WorksheetReport
from tabfree_schemas.bundle import check_bundle
from tabfree_schemas.validate import SchemaKind, schema_errors

ROOT = Path(__file__).resolve().parents[3]
EBIKES = (
    ROOT / "corpus" / "workbooks" / "tableau__e-bikes-demo" / "public" / "data" / "eBike Sales Analysis.twbx"
)
APACHE = ROOT / "corpus" / "workbooks" / "tableau__Logshark" / "LogShark" / "Workbooks" / "Apache.twbx"


def ds(
    name: str, cls: str, *, server: str = "", dbname: str = "", table: str = "[public].[orders]"
) -> dict[str, Any]:
    return {
        "name": name,
        "caption": name.title(),
        "kind": "embedded",
        "connection": {"class": cls, "server": server, "dbname": dbname, "username": "svc", "password": "x"},
        "relation": {"type": "table", "name": "orders", "table": table},
        "columns": [],
    }


# -- P5.1 remap ---------------------------------------------------------------------------------------


def test_rules_map_tables_with_templates_and_keep_no_credentials() -> None:
    rules = Rules.from_document(
        {
            "schema": "tabfree/remap@0",
            "rules": [
                {
                    "match": {"class": "postgres", "server": "pg.prod.*"},
                    "target": {"engine": "athena", "database": "sales_lake", "table": "{schema}_{table}"},
                }
            ],
        }
    )
    res = resolve(ds("sales", "postgres", server="pg.prod.corp", dbname="sales"), rules)
    assert res.status == "resolved"
    assert res.connection == {"engine": "athena", "database": "sales_lake"}
    assert res.tables == {"orders": {"table": "public_orders"}}
    assert "svc" not in json.dumps(res.to_dict())
    assert "password" not in json.dumps(res.to_dict())


def test_unmatched_sources_go_to_the_decision_queue_with_options() -> None:
    rules = Rules.from_document({})
    res = resolve(ds("erp", "sqlserver", server="erp.corp", dbname="erp"), rules)
    assert res.status == "decision"
    assert res.source_key == "sqlserver://erp.corp/erp"
    assert res.options == ["federated", "replicate", "snapshot"]
    excel = resolve(ds("budget", "excel-direct", dbname="C:/budget.xlsx", table="[Sheet1$]"), rules)
    assert excel.options == ["snapshot", "replicate"]
    # An answer in the queue resolves it: federated query through a catalog.
    answered = Rules.from_document(
        {
            "decisions": {
                "sqlserver://erp.corp/erp": {"choice": "federated", "catalog": "erp_fed", "database": "dbo"}
            }
        }
    )
    res = resolve(ds("erp", "sqlserver", server="erp.corp", dbname="erp"), answered)
    assert res.status == "resolved"
    assert res.connection == {"engine": "athena", "database": "dbo", "catalog": "erp_fed"}


def test_native_engines_and_converted_extracts() -> None:
    res = resolve(ds("dw", "redshift", server="rs.aws", dbname="analytics", table="[mart].[sales]"), Rules())
    assert res.connection["engine"] == "redshift"
    assert res.connection["database"] == "analytics"
    catalogue = ExtractCatalogue.from_reports(
        [
            {
                "status": "converted",
                "member": "Data/Data/ApacheRequests.hyper",
                "tables": [{"table": "ApacheRequests", "glue_table": "apacherequests_1a2b3c4d"}],
            }
        ]
    )
    apache = next(d for d in parse_file(APACHE)["datasources"] if d.get("kind") != "parameters")
    assert leaves(apache)[0].cls == "hyper"
    rules = Rules.from_document({"rules": [{"match": {"class": "hyper"}, "target": {"extract": True}}]})
    res = resolve(apache, rules, extracts=catalogue)
    assert res.status == "resolved"
    assert res.tables == {"ApacheRequests": {"table": "apacherequests_1a2b3c4d"}}
    assert res.connection == {"engine": "athena", "database": "tabfree_extracts"}


def test_bad_rule_files_are_refused() -> None:
    with pytest.raises(RemapError):
        Rules.from_document({"rules": [{"match": {"colour": "x"}, "target": {}}]})
    with pytest.raises(RemapError):
        Rules.from_document({"rules": [{"match": {"class": "x"}}]})
    with pytest.raises(RemapError):
        Rules.from_document({"decisions": {"a": {"choice": "magic"}}})


# -- P5.1 estate, P5.2 manifests, P5.3 baseline -------------------------------------------------------


@pytest.fixture(scope="module")
def ebikes() -> SourceDoc:
    return SourceDoc("wb-ebikes", parse_file(EBIKES), name="eBike Sales Analysis", luid="wb-ebikes")


def test_a_published_source_that_was_not_harvested_gets_a_model_from_the_workbook_copy(
    ebikes: SourceDoc,
) -> None:
    estate = generate_estate([], [ebikes], Rules())
    (entry,) = estate.models.values()
    assert entry.published
    assert entry.resolution.status == "decision"
    assert any("not harvested" in r for r in entry.report.review)
    (queued,) = estate.decision_queue()
    assert queued["sourceKey"] == "sqlproxy://ebikesinventoryandsales"
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, entry.doc) == []


def test_manifests_are_valid_and_carry_filters_parameters_and_actions(ebikes: SourceDoc) -> None:
    estate = generate_estate([], [ebikes], Rules())
    plans = plan_workbook(ebikes, estate)
    dashboard = next(p for p in plans if p.dashboard == "Sales Analysis")
    manifest = dashboard.manifest
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, manifest) == []
    assert manifest["id"] == "ebike-sales-analysis-sales-analysis"
    assert {"sales_ban", "sales_areatrend", "map"} <= set(manifest["queries"])
    assert "anchor_date" in manifest["parameters"]
    assert manifest["parameters"]["anchor_date"]["type"] == "date"
    assert any(a["type"] == "filter" for a in manifest.get("actions", []))
    assert manifest["source"]["dashboard"] == "Sales Analysis"
    # Deterministic: the same workbook gives the same manifest.
    again = next(p for p in plan_workbook(ebikes, generate_estate([], [ebikes], Rules())) if p.dashboard)
    assert again.manifest == manifest


def test_the_baseline_html_passes_the_bundle_check(ebikes: SourceDoc) -> None:
    estate = generate_estate([], [ebikes], Rules())
    plan = next(p for p in plan_workbook(ebikes, estate) if p.dashboard == "Sales Analysis")
    html = render(plan, package=EBIKES)
    report = check_bundle(plan.manifest, html)
    assert report.ok, report.errors
    assert '<tf-kpi query="sales_ban"' in html
    assert 'mark="area"' in html
    assert "<tf-map" in html
    assert "<tf-float" in html
    assert "<tf-container" in html
    assert 'src="data:image/png;base64,' in html  # the logo, from the packaged workbook
    assert "&lt;Sheet Name&gt;" in html  # workbook text is escaped, never markup
    notes = notes_markdown(plan)
    assert notes.startswith("# Sales Analysis")
    assert "## Not converted" in notes


def test_views_follow_marks_and_shelves() -> None:
    def sheet(spec: dict[str, Any], shelves: dict[str, list[str]], marks: list[str]) -> SheetPlan:
        return SheetPlan("Sheet", "q", None, spec, WorksheetReport(shelves=shelves), marks)

    bar = view_for(
        sheet(
            {"dimensions": ["Region"], "measures": ["Sales"]},
            {"rows": ["Region"], "cols": ["Sales"]},
            ["Bar"],
        )
    )
    assert (bar.tag, bar.attrs["x"], bar.attrs["y"], bar.attrs["mark"]) == (
        "tf-chart",
        "Sales",
        "Region",
        "bar",
    )
    line = view_for(
        sheet(
            {
                "dimensions": [{"field": "Date", "dateTrunc": "month", "as": "MONTH_TRUNC(Date)"}],
                "measures": ["Sales"],
            },
            {"cols": ["MONTH_TRUNC(Date)"], "rows": ["Sales"]},
            ["Automatic"],
        )
    )
    assert line.attrs["mark"] == "line"
    kpi = view_for(sheet({"measures": ["Sales", "Profit"]}, {"text": ["Profit"]}, ["Text"]))
    assert (kpi.tag, kpi.attrs["measure"]) == ("tf-kpi", "Profit")
    table = view_for(
        sheet(
            {"dimensions": ["Region", "Segment"], "measures": ["Sales"]},
            {"rows": ["Region"], "cols": ["Segment"], "text": ["Sales"]},
            ["Text"],
        )
    )
    assert table.tag == "tf-crosstab"
    assert table.attrs["columns"] == "Segment"
    assert view_for(sheet({}, {}, ["Bar"])).tag == "div"


# -- P5.4 governance ----------------------------------------------------------------------------------


def inventory() -> dict[str, list[dict[str, Any]]]:
    allow = lambda *caps: dict.fromkeys(caps, "Allow")  # noqa: E731
    return {
        "users": [
            {
                "id": "u1",
                "name": "CORP\\alice",
                "email": "Alice@Corp.example",
                "fullname": "Alice",
                "site_role": "Creator",
            },
            {"id": "u2", "name": "bob", "email": "", "fullname": "Bob", "site_role": "Viewer"},
            {"id": "u3", "name": "gone", "email": "gone@corp.example", "site_role": "Unlicensed"},
        ],
        "groups": [
            {
                "id": "g1",
                "name": "Sales",
                "domain_name": "corp.example",
                "minimum_site_role": "Explorer",
                "member_ids": ["u1", "u2"],
            },
            {"id": "g2", "name": "All Users", "domain_name": "local", "member_ids": ["u1", "u2", "u3"]},
        ],
        "projects": [
            {
                "id": "p1",
                "name": "Sales",
                "owner_id": "u1",
                "content_permissions": "LockedToProject",
                "permissions": [
                    {"grantee_type": "group", "grantee_id": "g1", "capabilities": allow("Read", "Write")},
                    {"grantee_type": "user", "grantee_id": "u1", "capabilities": allow("ProjectLeader")},
                ],
                "default_workbook_permissions": [
                    {
                        "grantee_type": "group",
                        "grantee_id": "g1",
                        "capabilities": {"Read": "Allow", "ExportData": "Deny", "RunExplainData": "Allow"},
                    },
                    {"grantee_type": "group", "grantee_id": "g-unknown", "capabilities": allow("Read")},
                ],
                "default_datasource_permissions": [
                    {"grantee_type": "group", "grantee_id": "g1", "capabilities": allow("Read", "Connect")}
                ],
            },
            {
                "id": "p2",
                "name": "Regional",
                "parent_id": "p1",
                "owner_id": "u3",
                "content_permissions": "ManagedByOwner",
            },
        ],
        "workbooks": [
            {
                "id": "w1",
                "name": "Superstore",
                "project_id": "p2",
                "owner_id": "u3",
                "tags": ["finance"],
                "description": "Sales overview",
                "permissions": [
                    {"grantee_type": "user", "grantee_id": "u2", "capabilities": allow("Read", "Filter")}
                ],
            }
        ],
        "views": [
            {"id": "v1", "name": "Overview", "workbook_id": "w1", "content_url": "Superstore/sheets/Overview"}
        ],
        "datasources": [
            {"id": "d1", "name": "Orders", "project_id": "p1", "owner_id": "u1", "certified": True}
        ],
        "schedules": [
            {"id": "s1", "name": "Daily", "frequency": "Daily", "frequency_details": {"start": "06:00"}}
        ],
        "subscriptions": [
            {
                "id": "sub1",
                "subject": "Daily sales",
                "user_id": "u2",
                "schedule_id": "s1",
                "target": {"id": "v1", "type": "View"},
            }
        ],
        "tasks": [
            {
                "id": "t1",
                "task_type": "extractRefresh",
                "target": {"id": "d1", "type": "datasource"},
                "schedule_id": "s1",
            }
        ],
        "favorites": [{"user_id": "u2", "views": ["v1"], "datasources": ["d1"]}],
        "user_workbooks": [{"user_id": "u2", "workbook_ids": ["w1"]}, {"user_id": "u1", "workbook_ids": []}],
    }


def test_governance_maps_people_projects_rules_and_extras() -> None:
    plan = plan_governance(
        inventory(),
        dashboards=[DashboardRef("superstore-overview", "w1", "Overview", ("Overview",))],
        models=[ModelRef("orders", "d1")],
        identity=Identity(by="email", domain="corp.example"),
        fallback_owner="migration-owner",
        repository={
            "users": [{"id": 7, "luid": "u2"}],
            "views": [{"id": 9, "luid": "v1"}],
            "data_alerts": [
                {
                    "id": 1,
                    "title": "Sales low",
                    "creator_id": 7,
                    "view_id": 9,
                    "operator": "below",
                    "threshold": "100",
                }
            ],
            "data_alerts_recipients": [{"data_alert_id": 1, "recipient_id": 7}],
        },
    )
    site = plan.site
    users = {u["username"]: u for u in site["users"]}
    assert set(users) == {"migration-owner", "alice@corp.example", "bob@corp.example", "gone@corp.example"}
    assert users["alice@corp.example"]["siteRole"] == "creator"
    assert users["alice@corp.example"]["groups"] == [
        "All Users",
        "Sales",
    ]
    projects = {p["id"]: p for p in site["projects"]}
    assert projects["sales"]["assetPermissions"] == "locked"
    assert projects["sales"]["leaders"] == ["alice@corp.example"]
    assert projects["sales"]["defaults"]["project"] == [
        {"group": "Sales", "capabilities": {"view": "allow", "publish": "allow"}}
    ]
    assert projects["sales"]["defaults"]["dashboard"] == [
        {"group": "Sales", "capabilities": {"view": "allow", "download_summary_data": "deny"}}
    ]
    assert projects["sales-regional"]["parent"] == "sales"
    # The owner is unlicensed: content and project fall back to the migration owner.
    assert projects["sales-regional"]["owner"] == "migration-owner"
    (dashboard,) = projects["sales-regional"]["dashboards"]
    assert dashboard == {
        "id": "superstore-overview",
        "owner": "migration-owner",
        "permissions": [{"user": "bob@corp.example", "capabilities": {"view": "allow", "filter": "allow"}}],
    }
    assert site["redirects"] == {"views/Superstore/Overview": "superstore-overview"}
    assert site["models"]["entries"][0]["permissions"] == [
        {"group": "Sales", "capabilities": {"view": "allow", "connect": "allow"}}
    ]
    extras = plan.extras
    assert extras["groups"][0] == {"name": "Sales", "source": "idp", "minimumSiteRole": "explorer"}
    assert sorted(f["id"] for f in extras["favorites"]["bob@corp.example"]) == [
        "orders",
        "superstore-overview",
    ]
    sub = next(j for j in extras["jobs"] if j["kind"] == "subscription")
    assert sub["dashboard"] == "superstore-overview"
    assert sub["schedule"]["frequency"] == "Daily"
    refresh = next(j for j in extras["jobs"] if j["kind"] == "refresh")
    assert refresh["model"] == "orders"
    assert refresh["status"] == "pending"
    (alert,) = extras["alerts"]
    assert alert["condition"] == {"operator": "<", "threshold": 100.0}
    assert alert["recipients"] == ["bob@corp.example"]
    assert extras["content"]["superstore-overview"] == {"tags": ["finance"], "description": "Sales overview"}
    assert extras["content"]["model:orders"] == {"certified": True}
    assert extras["access"] == {"alice@corp.example": [], "bob@corp.example": ["superstore-overview"]}
    report = plan.report
    assert {"where": "project 'Sales'", "group": "g-unknown"} in report["unmappedGrantees"]
    assert any(o["content"] == "dashboard 'superstore-overview'" for o in report["orphaned"])


def test_alert_conditions_decode_or_wait_for_the_owner() -> None:
    assert decode_alert({"condition_operator": "greater-than", "condition_value": "5"}) == {
        "operator": ">",
        "threshold": 5.0,
    }
    assert decode_alert({"condition": "<xml/>"}) is None


# -- P5.7 waves and archive ----------------------------------------------------------------------------


def test_waves_rank_by_usage_and_retire_unused() -> None:
    candidates = [Candidate(f"d{i}", f"D{i}", usage=i, converted=i != 3) for i in range(6)]
    waves = plan_waves(candidates, WaveSettings(sizes=(2, 2), retire_below=1))
    assert [w["dashboards"] for w in waves["waves"]] == [["d5", "d4"], ["d3", "d2"], ["d1"]]
    assert waves["retire"] == ["d0"]
    assert waves["items"]["d3"]["status"] == "manual"
    assert waves["items"]["d0"]["status"] == "retire"


def test_archive_copies_the_workbook_and_ground_truth(tmp_path: Path) -> None:
    raw, lake = LocalDirSink(tmp_path / "raw"), LocalDirSink(tmp_path / "lake")
    raw.put_bytes("blobs/aa/wb.twbx", b"workbook", "application/zip")
    raw.put_bytes("blobs/bb/view.png", b"png", "image/png")
    entry = archive(
        raw,
        lake,
        dashboard="old",
        workbook_key="blobs/aa/wb.twbx",
        ground_truth_keys=["blobs/bb/view.png", "blobs/cc/missing.csv"],
        reason="unused",
    )
    assert entry["kept"] == ["blobs/aa/wb.twbx", "blobs/bb/view.png"]
    assert entry["missing"] == ["blobs/cc/missing.csv"]
    assert lake.get_bytes("archive/old/blobs/aa/wb.twbx") == b"workbook"
    assert json.loads(lake.get_bytes("archive/index.json") or b"{}")["entries"]["old"]["reason"] == "unused"


# -- the run ---------------------------------------------------------------------------------------------


def test_factory_run_writes_an_importable_folder(tmp_path: Path) -> None:
    downloads = LocalDirSink(tmp_path / "raw")
    downloads.put_file("blobs/eb/ebikes.twbx", EBIKES, "application/zip")
    save_json(
        downloads,
        "downloads/manifest.json",
        {
            "entries": {
                "w1": {
                    "kind": "workbook",
                    "luid": "wb-ebikes",
                    "name": "eBike Sales Analysis",
                    "key": "blobs/eb/ebikes.twbx",
                }
            }
        },
    )
    png = b"\x89PNG\r\n\x1a\nfake"
    downloads.put_bytes("blobs/pp/sales.png", png, "image/png")
    downloads.put_bytes("blobs/cc/ban.csv", b"Sales\n100\n", "text/csv")
    captures = {
        "1": {
            "view_luid": "v1",
            "view_name": "Sales Analysis",
            "workbook_luid": "wb-ebikes",
            "format": "png",
            "key": "blobs/pp/sales.png",
        },
        "2": {
            "view_luid": "v2",
            "view_name": "sales_BAN",
            "workbook_luid": "wb-ebikes",
            "format": "csv",
            "key": "blobs/cc/ban.csv",
        },
    }
    save_json(downloads, "ground_truth/index.json", {"captures": captures})
    store = InventoryStore(tmp_path / "inventory.db")
    run = store.start_run("https://tableau.example", "site")
    for item in (
        Item(
            "users",
            "u1",
            "alice",
            None,
            {"name": "alice", "email": "alice@corp.example", "site_role": "Creator"},
        ),
        Item(
            "projects",
            "p1",
            "Sales",
            None,
            {"name": "Sales", "owner_id": "u1", "content_permissions": "ManagedByOwner"},
        ),
        Item(
            "workbooks",
            "wb-ebikes",
            "eBike Sales Analysis",
            None,
            {"project_id": "p1", "owner_id": "u1", "permissions": []},
        ),
        Item(
            "views",
            "v1",
            "Sales Analysis",
            None,
            {"workbook_id": "wb-ebikes", "content_url": "eBikeSales/sheets/SalesAnalysis", "total_views": 42},
        ),
    ):
        store.upsert("https://tableau.example", "site", item, run)
    store.db.commit()
    store.close()
    config = tmp_path / "factory.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema": "tabfree/factory@0",
                "site": "migrated",
                "downloads": "raw",
                "groundTruth": "raw",
                "inventory": "inventory.db",
                "waves": {"sizes": [1]},
            }
        ),
        encoding="utf-8",
    )
    result = run_factory(FactoryConfig.load(config), tmp_path / "out")
    out = result.out
    assert result.summary["workbooks"] == 1
    assert result.summary["converted"] >= 1
    folder = out / "dashboards" / "ebike-sales-analysis-sales-analysis"
    report = json.loads((folder / "report.json").read_text())
    assert report["status"] == "converted"
    assert report["usage"] == 42
    assert report["workbookLuid"] == "wb-ebikes"
    assert (folder / "reference.png").read_bytes() == png
    assert report["references"]["csv"]["sales_ban"]["file"] == "reference/sales_ban.csv"
    assert json.loads((folder / "source.json").read_text())["dashboard"]["name"] == "Sales Analysis"
    assert check_bundle(
        json.loads((folder / "tabfree.json").read_text()), (folder / "index.html").read_text()
    ).ok
    site = yaml.safe_load((out / "site.yaml").read_text())
    (project,) = site["projects"]
    assert project["id"] == "sales"
    assert "ebike-sales-analysis-sales-analysis" in [d["id"] for d in project["dashboards"]]
    assert site["redirects"]["views/eBikeSales/SalesAnalysis"] == "ebike-sales-analysis-sales-analysis"
    waves = json.loads((out / "waves.json").read_text())
    assert waves["waves"][0]["dashboards"] == ["ebike-sales-analysis-sales-analysis"]
    assert json.loads((out / "decisions.json").read_text())["queue"][0]["class"] == "sqlproxy"
    for model in (out / "models").glob("*.model.yaml"):
        assert schema_errors(SchemaKind.SEMANTIC_MODEL, yaml.safe_load(model.read_text())) == []


def test_factory_command_on_a_folder_of_workbooks(tmp_path: Path) -> None:
    (tmp_path / "files").mkdir()
    shutil.copy(EBIKES, tmp_path / "files" / EBIKES.name)
    (tmp_path / "factory.yaml").write_text("schema: tabfree/factory@0\ndownloads: files\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["factory", str(tmp_path / "factory.yaml"), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == 0, result.output
    assert "converted" in result.output
    assert "wave 1" in result.output
    bad = CliRunner().invoke(cli, ["factory", str(tmp_path / "factory.yaml.missing")])
    assert bad.exit_code != 0
    assert sqlite3  # the inventory is optional: no database was needed here


def test_crawler_reads_favorites_custom_views_and_workbooks_per_user() -> None:
    from types import SimpleNamespace

    from tabfree_migrate.inventory import OPTIONAL_TYPES
    from tabfree_migrate.inventory_tsc import TscInventorySource

    users = [SimpleNamespace(id="u1", name="alice")]

    class Users:
        def populate_workbooks(self, user: Any) -> None:
            user.workbooks = [SimpleNamespace(id="w2"), SimpleNamespace(id="w1")]

    class Favorites:
        def get(self, user: Any) -> None:
            user.favorites = {"views": [SimpleNamespace(id="v1")], "workbooks": [], "datasources": ["d1"]}

    source = TscInventorySource.__new__(TscInventorySource)
    source._srv = SimpleNamespace(users=Users(), favorites=Favorites(), custom_views="custom-views-endpoint")
    source._tsc = SimpleNamespace(
        Pager=lambda endpoint: iter(
            users if isinstance(endpoint, Users) else [SimpleNamespace(id="cv1", name="Mine", shared=True)]
        )
    )
    (fav,) = source.items("favorites")
    assert fav.data["views"] == ["v1"]
    assert fav.data["datasources"] == ["d1"]
    assert fav.data["projects"] == []
    (uw,) = source.items("user_workbooks")
    assert uw.data == {"user_id": "u1", "workbook_ids": ["w1", "w2"]}
    (cv,) = source.items("custom_views")
    assert (cv.type, cv.luid, cv.name) == ("custom_views", "cv1", "Mine")
    assert set(OPTIONAL_TYPES) == {"favorites", "custom_views", "user_workbooks"}
