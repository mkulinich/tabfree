"""convert-batch: discovery, resumability, process pool, sinks, .tde flagging (fake converter)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tabfree_migrate.blobstore import LocalDirSink, S3Sink, Sink, load_json, save_json
from tabfree_migrate.cli import app
from tabfree_migrate.convert_batch import (
    BatchOptions,
    ExtractSource,
    convert_batch,
    in_shard,
    manifest_key,
    parse_shard,
    report_key,
    sources_from_directory,
    sources_from_store,
    upgraded_key,
)
from tabfree_migrate.extract_format import TDE_MAGIC
from tabfree_migrate.testing import FakeS3Client, fake_convert_extract

HYPER_BYTES = b"Hyper\x08\x00\x00\x01" + b"\x00" * 64
TDE_BYTES = TDE_MAGIC + b"\x00" * 64

TWB = (
    "<?xml version='1.0'?><workbook version='18.1'><datasources>"
    "<datasource name='federated.1' caption='Orders'><connection class='federated'/>"
    "<extract enabled='true'><connection class='hyper' dbname='Data/Extracts/orders.hyper'/>"
    "<refresh increment-key='[Order Date]' incremental-updates='true'/>"
    "<filter class='categorical' column='[Region]'/></extract>"
    "</datasource></datasources></workbook>"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("orders.twb", TWB)
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def inputs(tmp_path: Path) -> Path:
    root = tmp_path / "in"
    (root / "nested").mkdir(parents=True)
    (root / "plain.hyper").write_bytes(HYPER_BYTES + b"plain")
    (root / "legacy.tde").write_bytes(TDE_BYTES)
    (root / "nested" / "notes.txt").write_text("ignored")
    _package(root / "nested" / "orders.twbx", {"Data/Extracts/orders.hyper": HYPER_BYTES + b"pkg"})
    return root


def _options(out: Path | str, tmp_path: Path, **kw: Any) -> BatchOptions:
    return BatchOptions(out=str(out), work_dir=str(tmp_path / "work"), converter=fake_convert_extract, **kw)


def _reports(sink: Sink, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {sha: load_json(sink, report_key(sha)) for sha in manifest["entries"]}


def test_directory_batch_converts_flags_and_records(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    stats = convert_batch(sources_from_directory(inputs), _options(out, tmp_path, database="lake_db"))
    assert (stats.converted, stats.flagged, stats.failed, stats.skipped) == (2, 1, 0, 0)

    sink = LocalDirSink(out)
    manifest = load_json(sink, "extracts/manifest.json")
    reports = _reports(sink, manifest)
    by_status = {r["status"]: r for r in reports.values() if r["format"] != "hyper"}
    tde = by_status["needs-upgrade"]
    assert tde["format"] == "tde"
    assert tde["advice"]["options"]

    member_sha = _sha(HYPER_BYTES + b"pkg")
    packaged = reports[member_sha]
    assert packaged["member"] == "Data/Extracts/orders.hyper"
    assert packaged["settings"]["incremental_key"] == "[Order Date]"
    (table,) = packaged["tables"]
    assert table["glue_table"] == f"orders_{member_sha[:8]}__extract"
    assert sink.exists(table["parquet"])
    assert table["parquet"] == f"extracts/{member_sha}/{table['glue_table']}/data.parquet"
    assert table["definition"]["incremental"]["glue_column"] == "order_date"
    assert table["definition"]["filters"]["tableau"][0]["column"] == "[Region]"
    assert table["glue_parameters"]["tabfree.extract.incremental_key"] == "order_date"
    assert table["glue_parameters"]["tabfree.geo.location.srid"] == "4326"
    staging_ddl, ctas, _drop = table["athena_ddl"]
    assert "TBLPROPERTIES" in staging_ddl
    assert "'tabfree.extract.incremental_key' = 'order_date'" in staging_ddl
    assert f"`lake_db`.`{table['glue_table']}`" in ctas
    assert "s3://<lake-bucket>/warehouse/lake_db/" in ctas
    assert table["athena_parity_sql"].endswith(f'FROM "lake_db"."{table["glue_table"]}"')
    assert table["definition"]["iceberg_ddl"].startswith("CREATE TABLE `lake_db`")

    bare = reports[_sha(HYPER_BYTES + b"plain")]
    assert bare["settings"] is None
    assert any("no extract settings" in w for w in bare["warnings"])
    # Staged package members are cleaned up.
    assert not (tmp_path / "work" / "staging").exists()


def test_rerun_skips_finished_work_and_force_redoes_it(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    convert_batch(sources_from_directory(inputs), _options(out, tmp_path))
    again = convert_batch(sources_from_directory(inputs), _options(out, tmp_path))
    assert (again.converted, again.flagged, again.skipped) == (0, 0, 3)

    # A lost report means the extract is converted again.
    (out / report_key(_sha(HYPER_BYTES + b"plain"))).unlink()
    third = convert_batch(sources_from_directory(inputs), _options(out, tmp_path))
    assert (third.converted, third.skipped) == (1, 2)

    forced = convert_batch(sources_from_directory(inputs), _options(out, tmp_path), force=True)
    assert (forced.converted, forced.flagged, forced.skipped) == (2, 1, 0)


def test_failures_are_reported_and_retried(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    (root / "bad.hyper").write_bytes(HYPER_BYTES + b"FAIL")
    (root / "broken.twbx").write_bytes(b"PK\x03\x04 not really a zip")
    (root / "text.hyper").write_bytes(b"not an extract")
    out = tmp_path / "out"
    stats = convert_batch(sources_from_directory(root), _options(out, tmp_path))
    assert stats.failed == 3
    manifest = load_json(LocalDirSink(out), "extracts/manifest.json")
    statuses = sorted(e["status"] for e in manifest["entries"].values())
    assert statuses == ["failed", "unsupported"]
    assert any("error" in s for s in manifest["sources"].values())  # the broken package
    failed = next(e for e in manifest["entries"].values() if e["status"] == "failed")
    assert "fake conversion failure" in failed["error"]
    again = convert_batch(sources_from_directory(root), _options(out, tmp_path))
    assert (again.failed, again.skipped) == (2, 1)  # failures are retried, unsupported is final


def test_process_pool(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    stats = convert_batch(sources_from_directory(inputs), _options(out, tmp_path), workers=2)
    assert (stats.converted, stats.flagged, stats.failed) == (2, 1, 0)
    manifest = load_json(LocalDirSink(out), "extracts/manifest.json")
    assert len(manifest["entries"]) == 3


def test_shards_split_the_work_and_keep_separate_manifests(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    sources = sources_from_directory(inputs)
    totals = 0
    for index in range(2):
        stats = convert_batch(sources, _options(out, tmp_path), shard=(index, 2))
        totals += stats.converted + stats.flagged
        assert LocalDirSink(out).exists(manifest_key((index, 2)))
    assert totals == 3
    assert sum(in_shard(s.source_id, (0, 2)) for s in sources) + sum(
        in_shard(s.source_id, (1, 2)) for s in sources
    ) == len(sources)
    assert parse_shard("1/4") == (1, 4)
    for bad in ("4/4", "-1/2", "x"):
        with pytest.raises(ValueError):  # noqa: PT011 - int() and the range check raise different messages
            parse_shard(bad)


def test_store_input_and_s3_output(tmp_path: Path) -> None:
    store = LocalDirSink(tmp_path / "store")
    package = _package(tmp_path / "pkg.tdsx", {"Data/Extracts/orders.hyper": HYPER_BYTES + b"s3"})
    data = package.read_bytes()
    key = f"blobs/sha256/{_sha(data)[:2]}/{_sha(data)}.tdsx"
    store.put_bytes(key, data, "application/octet-stream")
    save_json(
        store,
        "downloads/manifest.json",
        {
            "entries": {
                "datasources/l1/with-extract": {
                    "name": "Orders", "include_extract": True, "sha256": _sha(data), "key": key,
                },
                "datasources/l1/no-extract": {
                    "name": "Orders", "include_extract": False, "sha256": "x", "key": "k",
                },
                "datasources/l2/with-extract": {
                    "name": "Broken", "include_extract": True, "error": "HTTP 500",
                },
            }
        },
    )  # fmt: skip
    sources = sources_from_store(store)
    assert [s.name for s in sources] == ["Orders.tdsx"]

    client = FakeS3Client()

    def s3(location: str) -> Sink:
        return S3Sink.from_url(location, client=client)

    options = _options("s3://lake/staging", tmp_path, sink_factory=s3, database="db")
    stats = convert_batch(sources, options, store=store)
    assert stats.converted == 1
    member_sha = _sha(HYPER_BYTES + b"s3")
    report = json.loads(client.objects[("lake", f"staging/{report_key(member_sha)}")][0])
    (table,) = report["tables"]
    assert ("lake", f"staging/{table['parquet']}") in client.objects
    assert table["staging_location"] == f"s3://lake/staging/extracts/{member_sha}/{table['glue_table']}/"
    assert table["iceberg_location"] == f"s3://lake/staging/warehouse/db/{table['glue_table']}/"
    assert not any("local output" in w for w in report["warnings"])
    rerun = convert_batch(sources_from_store(store), options, store=store)
    assert (rerun.converted, rerun.skipped) == (0, 1)


def test_tde_upgrade_mode_without_legacy_api_still_flags(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    stats = convert_batch(sources_from_directory(inputs), _options(out, tmp_path, tde_mode="upgrade"))
    assert stats.flagged == 1
    sink = LocalDirSink(out)
    report = load_json(sink, report_key(_sha(TDE_BYTES)))
    assert "legacy image" in report["advice"]["note"]


def test_upgraded_tde_is_converted_by_the_normal_image(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    sink = LocalDirSink(out)
    convert_batch(sources_from_directory(inputs), _options(out, tmp_path))
    # What the legacy image leaves behind: upgraded/<sha>.hyper
    sink.put_bytes(upgraded_key(_sha(TDE_BYTES)), HYPER_BYTES + b"upgraded", "application/octet-stream")
    stats = convert_batch(sources_from_directory(inputs), _options(out, tmp_path))
    assert (stats.converted, stats.skipped) == (1, 2)
    report = load_json(sink, report_key(_sha(TDE_BYTES)))
    assert report["status"] == "converted"
    assert report["upgraded_from_tde"] == upgraded_key(_sha(TDE_BYTES))


def test_source_without_path_or_store_fails_cleanly(tmp_path: Path) -> None:
    source = ExtractSource("0" * 64, "nowhere", "x.hyper")
    stats = convert_batch([source], _options(tmp_path / "out", tmp_path))
    assert stats.failed == 1


def test_cli_convert_batch_and_parity_check(
    inputs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tabfree_migrate.hyper.convert_hyper", fake_convert_extract)
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["convert-batch", "--input-dir", str(inputs), "--out", str(out), "--workers", "1",
         "--work-dir", str(tmp_path / "work")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "converted 2, skipped 0, needs-upgrade 1, upgraded 0, failed 0" in result.output
    assert "needs-upgrade" in result.output

    bad = runner.invoke(app, ["convert-batch", "--out", str(out)])
    assert bad.exit_code == 2
    bad_shard = runner.invoke(
        app, ["convert-batch", "--input-dir", str(inputs), "--out", str(out), "--shard", "3/2"]
    )
    assert bad_shard.exit_code == 2
    bad_tde = runner.invoke(
        app, ["convert-batch", "--input-dir", str(inputs), "--out", str(out), "--tde", "x"]
    )
    assert bad_tde.exit_code == 2

    missing_root = runner.invoke(app, ["parity-check", str(out / report_key(_sha(TDE_BYTES)))])
    assert missing_root.exit_code == 2
