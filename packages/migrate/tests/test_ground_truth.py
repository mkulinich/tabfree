from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
import tableauserverclient as tsc

from tabfree_migrate.blobstore import LocalDirSink, load_json
from tabfree_migrate.ground_truth import (
    FORMATS,
    INDEX_KEY,
    ExportOptions,
    Permutation,
    TscViewExporter,
    ViewTarget,
    capture_all,
    capture_id,
    load_permutations,
    parameter_key,
    plan_captures,
    targets_from_inventory,
)
from tabfree_migrate.inventory import InventoryStore, Item
from tabfree_migrate.testing import FakeViewExporter

VIEWS = [
    ViewTarget("v1", "Sales", "w1", "2026-02-01T00:00:00Z", usage=120),
    ViewTarget("v2", "Ops", "w1", "2026-02-01T00:00:00Z", usage=5),
]


def _no_sleep(_seconds: float) -> None:
    return None


def test_query_params_are_fixed_and_explicit() -> None:
    options = ExportOptions(
        viz_width=1600,
        viz_height=900,
        max_age=5,
        page_type="a4",
        orientation="landscape",
        filters=(("Region", "East"),),
        parameters=(("Top N", "10"),),
    )
    assert options.query_params("png") == {
        "maxAge": "5",
        "vf_Region": "East",
        "vf_Parameters.Top N": "10",
        "vizWidth": "1600",
        "vizHeight": "900",
        "resolution": "high",
    }
    pdf = options.query_params("pdf")
    assert (pdf["type"], pdf["orientation"], pdf["vizWidth"]) == ("a4", "landscape", "1600")
    assert "vizWidth" not in options.query_params("csv")
    assert options.query_params("xlsx") == {"maxAge": "5", "vf_Region": "East", "vf_Parameters.Top N": "10"}
    with pytest.raises(ValueError, match="unknown export format"):
        options.query_params("svg")
    assert parameter_key("Parameters.X") == "vf_Parameters.X"
    assert parameter_key("vf_Parameters.X") == "vf_Parameters.X"


@pytest.mark.parametrize("fmt", FORMATS)
def test_our_params_match_what_tsc_sends(fmt: str) -> None:
    options = ExportOptions(
        max_age=1,
        page_type="a4",
        orientation="portrait",
        filters=(("Region", "East"),),
        parameters=(("Top N", "10"),),
    )
    request = TscViewExporter(object(), tsc)._request_options(fmt, options)
    sent = {k: str(v) for k, v in request.get_query_params().items()}
    assert sent == options.query_params(fmt)


def test_permutations_file_parsing() -> None:
    perms = load_permutations(
        {
            "default": [{"label": "east", "filters": {"Region": "East"}}],
            "views": {"v1": [{"parameters": {"Top N": "5"}}]},
        }
    )
    assert perms["*"] == [Permutation("east", {"Region": "East"}, {})]
    assert perms["v1"] == [Permutation("p0", {}, {"Top N": "5"})]
    with pytest.raises(ValueError, match="strings"):
        load_permutations({"default": [{"filters": {"Year": 2024}}]})
    with pytest.raises(ValueError, match="lists"):
        load_permutations({"default": {"filters": {}}})


def test_plan_permutes_only_top_views() -> None:
    perms = {"*": [Permutation("east", {"Region": "East"})], "v2": [Permutation("ops", {"Team": "A"})]}
    plan = plan_captures(VIEWS, ExportOptions(), formats=("png", "csv"), permutations=perms, top=1)
    labels = [(t.luid, fmt, label) for t, fmt, label, _ in plan]
    assert labels == [
        ("v1", "png", "base"),
        ("v1", "csv", "base"),
        ("v2", "png", "base"),
        ("v2", "csv", "base"),
        ("v1", "png", "east"),
        ("v1", "csv", "east"),
    ]
    assert plan[-1][3].filters == (("Region", "East"),)


def test_capture_stores_blobs_and_reproducible_index(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path / "gt")
    exporter = FakeViewExporter()
    plan = plan_captures(
        VIEWS, ExportOptions(), permutations={"*": [Permutation("east", {"Region": "East"})]}
    )
    stats = capture_all(exporter, sink, plan, staging=tmp_path)
    assert stats.captured == len(plan) == 2 * 4 + 2 * 1 * 2
    index = load_json(sink, INDEX_KEY)["captures"]
    base_png = index[capture_id("v1", "png", ExportOptions())]
    assert base_png["request"] == {
        "path": "views/v1/image",
        "params": {"vizWidth": "1920", "vizHeight": "1080", "resolution": "high"},
    }
    assert base_png["options"]["viz_width"] == 1920
    assert base_png["workbook_updated_at"] == "2026-02-01T00:00:00Z"
    assert base_png["key"].endswith(".png")
    assert sink.get_bytes(base_png["key"]) is not None
    east = [c for c in index.values() if c["label"] == "east" and c["format"] == "csv"]
    assert {c["request"]["params"]["vf_Region"] for c in east} == {"East"}
    crosstab = index[capture_id("v2", "xlsx", ExportOptions())]
    assert crosstab["request"]["path"] == "views/v2/crosstab/excel"

    again = FakeViewExporter()
    assert capture_all(again, sink, plan, staging=tmp_path).skipped == len(plan)
    assert again.calls == []


def test_capture_failures_are_recorded_and_retried_next_run(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path / "gt")
    exporter = FakeViewExporter()
    exporter.broken.add(("v2", "pdf"))
    plan = plan_captures(VIEWS, ExportOptions())
    stats = capture_all(exporter, sink, plan, staging=tmp_path, sleep=_no_sleep)
    cid = capture_id("v2", "pdf", ExportOptions())
    assert stats.errors == {cid: "PermissionError: 403 exporting pdf"}
    assert load_json(sink, INDEX_KEY)["captures"][cid]["error"].startswith("PermissionError")

    fixed = FakeViewExporter()
    stats = capture_all(fixed, sink, plan, staging=tmp_path)
    assert (stats.captured, stats.skipped) == (1, len(plan) - 1)

    changed = [ViewTarget("v1", "Sales", "w1", "2026-05-01T00:00:00Z", 120)]
    assert capture_all(FakeViewExporter(), sink, plan_captures(changed, ExportOptions())).captured == 4


def test_empty_export_is_an_error(tmp_path: Path) -> None:
    class Empty(FakeViewExporter):
        def export(self, view_luid: str, fmt: str, options: ExportOptions, out: Any) -> None:
            return None

    plan = plan_captures(VIEWS[:1], ExportOptions(), formats=("csv",))
    stats = capture_all(Empty(), LocalDirSink(tmp_path / "gt"), plan, staging=tmp_path)
    assert list(stats.errors.values()) == ["ValueError: empty export"]


def test_targets_from_inventory_rank_by_usage(tmp_path: Path) -> None:
    store = InventoryStore(tmp_path / "inventory.db")
    run = store.start_run("s", "site")
    store.upsert("s", "site", Item("workbooks", "w1", "Book", "2026-01-01", {}), run)
    store.upsert("s", "site", Item("views", "v1", "A", None, {"workbook_id": "w1", "total_views": 3}), run)
    store.upsert("s", "site", Item("views", "v2", "B", None, {"workbook_id": "w1", "total_views": 9}), run)
    store.upsert("s", "site", Item("views", "v3", "C", None, {}), run)
    assert [t.luid for t in targets_from_inventory(store)] == ["v2", "v1", "v3"]
    ranked = targets_from_inventory(store, {"v1": 100})
    assert [(t.luid, t.usage) for t in ranked] == [("v1", 100), ("v2", 0), ("v3", 0)]
    assert ranked[0].workbook_updated_at == "2026-01-01"
    assert ranked[2].workbook_luid is None


def test_tsc_exporter_uses_view_exports() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class View:
        image = b"png-bytes"
        pdf = b"pdf-bytes"
        csv = iter([b"a,b\n", b"1,2\n"])
        excel = iter([b"xlsx"])

    class Views:
        def get_by_id(self, luid: str) -> View:
            return View()

        def _record(self, kind: str, req: Any) -> None:
            seen.append((kind, req.get_query_params()))

        def populate_image(self, view: View, req: Any) -> None:
            self._record("image", req)

        def populate_pdf(self, view: View, req: Any) -> None:
            self._record("pdf", req)

        def populate_csv(self, view: View, req: Any) -> None:
            self._record("csv", req)

        def populate_excel(self, view: View, req: Any) -> None:
            self._record("excel", req)

    class Server:
        views = Views()

    exporter = TscViewExporter(Server(), tsc)
    outputs = {}
    for fmt in FORMATS:
        out = io.BytesIO()
        exporter.export("v1", fmt, ExportOptions(filters=(("Region", "East"),)), out)
        outputs[fmt] = out.getvalue()
    assert outputs == {"png": b"png-bytes", "pdf": b"pdf-bytes", "csv": b"a,b\n1,2\n", "xlsx": b"xlsx"}
    assert [kind for kind, _ in seen] == ["image", "pdf", "csv", "excel"]
    assert all(params["vf_Region"] == "East" for _, params in seen)
    with pytest.raises(ValueError, match="unknown export format"):
        exporter._request_options("svg", ExportOptions())
