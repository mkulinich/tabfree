from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate.blobstore import LocalDirSink, S3Sink, load_json
from tabfree_migrate.downloads import (
    MANIFEST_KEY,
    DownloadTarget,
    TscDownloader,
    all_extracts,
    download_all,
    extension_for,
    extracts_for,
    targets_from_inventory,
)
from tabfree_migrate.inventory import InventoryStore, Item
from tabfree_migrate.testing import FakeDownloader, FakeS3Client

TWBX = b"PK\x03\x04 workbook package"
TWBX_FULL = b"PK\x03\x04 workbook package with a large extract"
TDS = b"<?xml version='1.0'?><datasource/>"


def _targets() -> list[DownloadTarget]:
    return [
        DownloadTarget("workbooks", "w1", "Superstore", "2026-02-01T00:00:00Z"),
        DownloadTarget("datasources", "d1", "Orders", "2026-02-02T00:00:00Z"),
    ]


def _downloader() -> FakeDownloader:
    return FakeDownloader(
        {
            ("workbooks", "w1", False): TWBX,
            ("workbooks", "w1", True): TWBX_FULL,
            ("datasources", "d1", False): TDS,
            ("datasources", "d1", True): TDS,
        }
    )


def _no_sleep(_seconds: float) -> None:
    return None


def test_extension_is_decided_from_magic_bytes_only() -> None:
    assert extension_for("workbooks", TWBX) == ".twbx"
    assert extension_for("workbooks", b"<?xml") == ".twb"
    assert extension_for("datasources", b"PK\x03\x04") == ".tdsx"
    assert extension_for("datasources", b"") == ".tds"


def test_downloads_without_extracts_first_and_writes_manifest(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path / "raw")
    downloader = _downloader()
    stats = download_all(downloader, sink, _targets(), extracts_for(["w1"]), staging=tmp_path)
    assert (stats.downloaded, stats.skipped, stats.errors) == (3, 0, {})
    assert downloader.calls == [
        ("workbooks", "w1", False),
        ("datasources", "d1", False),
        ("workbooks", "w1", True),
    ]
    manifest = load_json(sink, MANIFEST_KEY)["entries"]
    entry = manifest["workbooks/w1/no-extract"]
    assert entry["sha256"] == hashlib.sha256(TWBX).hexdigest()
    assert entry["key"].endswith(".twbx")
    assert entry["size"] == len(TWBX)
    assert entry["updated_at"] == "2026-02-01T00:00:00Z"
    assert manifest["datasources/d1/no-extract"]["key"].endswith(".tds")
    assert manifest["workbooks/w1/with-extract"]["include_extract"] is True
    assert sink.get_bytes(entry["key"]) == TWBX


def test_rerun_skips_unchanged_and_refetches_changed_or_missing(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path / "raw")
    download_all(_downloader(), sink, _targets(), staging=tmp_path)
    again = _downloader()
    stats = download_all(again, sink, _targets(), staging=tmp_path)
    assert (stats.downloaded, stats.skipped) == (0, 2)
    assert again.calls == []

    changed = [DownloadTarget("workbooks", "w1", "Superstore", "2026-03-01T00:00:00Z"), _targets()[1]]
    blob = load_json(sink, MANIFEST_KEY)["entries"]["datasources/d1/no-extract"]["key"]
    (tmp_path / "raw" / blob).unlink()
    third = _downloader()
    stats = download_all(third, sink, changed, staging=tmp_path)
    assert stats.downloaded == 2
    assert download_all(_downloader(), sink, changed, force=True, staging=tmp_path).downloaded == 2


def test_failures_are_recorded_retried_and_resumed(tmp_path: Path) -> None:
    class ThrottledError(Exception):
        code = "429000"

    sink = LocalDirSink(tmp_path / "raw")
    downloader = _downloader()
    downloader.failures[("workbooks", "w1", False)] = [ThrottledError("slow down")]
    downloader.failures[("datasources", "d1", False)] = [PermissionError("403")]
    stats = download_all(downloader, sink, _targets(), staging=tmp_path, sleep=_no_sleep)
    assert stats.downloaded == 1
    assert stats.errors == {"datasources/d1/no-extract": "PermissionError: 403"}
    workbook = load_json(sink, MANIFEST_KEY)["entries"]["workbooks/w1/no-extract"]
    assert sink.get_bytes(workbook["key"]) == TWBX  # the retry did not keep the partial bytes

    resumed = _downloader()
    stats = download_all(resumed, sink, _targets(), staging=tmp_path)
    assert (stats.downloaded, stats.skipped) == (1, 1)
    assert resumed.calls == [("datasources", "d1", False)]


def test_manifest_is_saved_even_when_the_run_dies(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path / "raw")
    downloader = _downloader()
    downloader.failures[("datasources", "d1", False)] = [KeyboardInterrupt()]  # type: ignore[list-item]
    with pytest.raises(KeyboardInterrupt):
        download_all(downloader, sink, _targets(), save_every=100, staging=tmp_path)
    assert list(load_json(sink, MANIFEST_KEY)["entries"]) == ["workbooks/w1/no-extract"]


def test_s3_sink_and_all_extracts(tmp_path: Path) -> None:
    client = FakeS3Client()
    sink = S3Sink("raw", "tableau", client=client)
    stats = download_all(_downloader(), sink, _targets(), all_extracts, staging=tmp_path)
    assert stats.downloaded == 4
    keys = sorted(key for _bucket, key in client.objects)
    assert "tableau/downloads/manifest.json" in keys
    assert len([k for k in keys if k.startswith("tableau/blobs/")]) == 3  # identical .tds stored once


def test_targets_from_inventory(tmp_path: Path) -> None:
    store = InventoryStore(tmp_path / "inventory.db")
    run = store.start_run("s", "site")
    store.upsert("s", "site", Item("workbooks", "w2", "Big", "t2", {"size": 900}), run)
    store.upsert("s", "site", Item("workbooks", "w1", "Small", "t1", {"size": 10}), run)
    store.upsert("s", "site", Item("datasources", "d1", "Orders", "t3", {}), run)
    store.upsert("s", "site", Item("views", "v1", "View", None, {}), run)
    targets = targets_from_inventory(store)
    assert [t.luid for t in targets] == ["w1", "w2", "d1"]
    assert targets[1].size == 900
    with pytest.raises(ValueError, match="cannot download 'views'"):
        targets_from_inventory(store, ["views"])


def test_tsc_downloader_gets_a_file_object_tsc_accepts(tmp_path: Path) -> None:
    """TSC streams only into io.BytesIO/io.BufferedWriter; anything else is treated as a path."""

    class Endpoint:
        def download(self, luid: str, filepath: Any, include_extract: bool) -> Any:
            if not isinstance(filepath, io.BytesIO | io.BufferedWriter):
                raise AssertionError("TSC would treat this as a path and write a server-named file")
            for chunk in (b"PK\x03\x04", b"rest"):
                filepath.write(chunk)
            return filepath

    class Server:
        workbooks = Endpoint()
        datasources = Endpoint()

    sink = LocalDirSink(tmp_path / "raw")
    stats = download_all(TscDownloader(Server()), sink, _targets()[:1], staging=tmp_path)
    assert stats.errors == {}
    entry = load_json(sink, MANIFEST_KEY)["entries"]["workbooks/w1/no-extract"]
    assert entry["sha256"] == hashlib.sha256(b"PK\x03\x04rest").hexdigest()
    assert sink.get_bytes(entry["key"]) == b"PK\x03\x04rest"
    assert list(tmp_path.iterdir()) == [tmp_path / "raw"]


def test_tsc_downloader_streams_into_our_file_object() -> None:
    calls: list[dict[str, Any]] = []

    class Endpoint:
        def download(self, luid: str, filepath: Any, include_extract: bool) -> Any:
            calls.append({"luid": luid, "include_extract": include_extract})
            filepath.write(b"PK\x03\x04")
            return filepath

    class Server:
        workbooks = Endpoint()
        datasources = Endpoint()

    out = io.BytesIO()
    TscDownloader(Server()).download("workbooks", "w1", False, out)
    assert out.getvalue() == b"PK\x03\x04"
    assert calls == [{"luid": "w1", "include_extract": False}]
    with pytest.raises(ValueError, match="cannot download"):
        TscDownloader(Server()).download("flows", "f1", False, out)
