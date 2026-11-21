from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

import pytest

from tabfree_migrate import blobstore
from tabfree_migrate.blobstore import (
    LocalDirSink,
    S3Sink,
    blob_key,
    load_json,
    save_json,
    sink_for,
    store_chunks,
)
from tabfree_migrate.retry import is_transient, status_of, with_retries
from tabfree_migrate.testing import FakeS3Client


def test_local_sink_stores_content_addressed_once(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path / "out")
    ref = store_chunks(sink, [b"hello ", b"world"], ".csv", staging=tmp_path)
    digest = hashlib.sha256(b"hello world").hexdigest()
    assert ref.sha256 == digest
    assert ref.size == 11
    assert ref.key == blob_key(digest, ".csv") == f"blobs/sha256/{digest[:2]}/{digest}.csv"
    assert ref.content_type == "text/csv"
    assert sink.get_bytes(ref.key) == b"hello world"
    again = store_chunks(sink, [b"hello world"], ".csv", staging=tmp_path)
    assert again == ref
    assert list(tmp_path.glob("tabfree-*.part")) == []


def test_local_sink_rejects_path_traversal(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path)
    for key in ("../escape", "/abs", "a//b", "a/./b", ""):
        with pytest.raises(ValueError, match="unsafe storage key"):
            sink.put_bytes(key, b"x", "text/plain")


def test_json_documents_round_trip(tmp_path: Path) -> None:
    sink = LocalDirSink(tmp_path)
    assert load_json(sink, "downloads/manifest.json") == {}
    save_json(sink, "downloads/manifest.json", {"entries": {"a": 1}})
    assert load_json(sink, "downloads/manifest.json") == {"entries": {"a": 1}}
    sink.put_bytes("bad.json", b"[1]", "application/json")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_json(sink, "bad.json")


def test_s3_sink_uses_prefix_and_fake_client(tmp_path: Path) -> None:
    client = FakeS3Client()
    sink = S3Sink.from_url("s3://raw-zone/tableau/prod", client=client)
    assert sink.describe() == "s3://raw-zone/tableau/prod"
    ref = store_chunks(sink, [b"PK\x03\x04data"], ".twbx", staging=tmp_path)
    stored, ctype = client.objects[("raw-zone", f"tableau/prod/{ref.key}")]
    assert stored == b"PK\x03\x04data"
    assert ctype == "application/octet-stream"
    assert sink.exists(ref.key)
    assert not sink.exists("blobs/missing")
    assert sink.get_bytes("blobs/missing") is None
    save_json(sink, "ground_truth/index.json", {"captures": {}})
    assert load_json(sink, "ground_truth/index.json") == {"captures": {}}


def test_s3_sink_reraises_other_errors() -> None:
    class DeniedError(Exception):
        response: dict[str, Any] = {"Error": {"Code": "AccessDeniedError"}}  # noqa: RUF012

    class Client(FakeS3Client):
        def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            raise DeniedError()

    with pytest.raises(DeniedError):
        S3Sink("bucket", client=Client()).exists("a")


def test_s3_location_validation_and_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="s3://bucket"):
        S3Sink.from_url("https://bucket")
    with pytest.raises(ValueError, match="no bucket"):
        S3Sink.from_url("s3:///prefix")

    def no_boto(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", no_boto)
    with pytest.raises(RuntimeError, match=r"tabfree-migrate\[aws\]"):
        sink_for("s3://bucket/prefix")


def test_sink_for_local_directory(tmp_path: Path) -> None:
    sink = sink_for(str(tmp_path / "local"))
    assert isinstance(sink, LocalDirSink)
    assert sink.describe() == str(tmp_path / "local")


def test_content_types() -> None:
    assert blobstore.content_type_for(".png") == "image/png"
    assert blobstore.content_type_for(".twbx") == "application/octet-stream"


class _ThrottledError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def test_retry_backs_off_on_429_and_5xx_only() -> None:
    delays: list[float] = []
    calls = iter([_ThrottledError("429000"), _ThrottledError("500001"), "ok"])

    def call() -> str:
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    assert with_retries(call, sleep=delays.append) == "ok"
    assert delays == [2.0, 4.0]

    def forbidden() -> str:
        raise _ThrottledError("403004")

    with pytest.raises(_ThrottledError):
        with_retries(forbidden, sleep=delays.append)
    assert len(delays) == 2


def test_retry_gives_up_after_attempts() -> None:
    delays: list[float] = []

    def always() -> None:
        raise _ThrottledError("503")

    with pytest.raises(_ThrottledError):
        with_retries(always, attempts=3, sleep=delays.append)
    assert delays == [2.0, 4.0]


def test_status_detection() -> None:
    class Response:
        status_code = 502

    class WithResponseError(Exception):
        response = Response()

    assert status_of(WithResponseError()) == 502
    assert status_of(ValueError("x")) is None
    assert status_of(_ThrottledError("ab")) is None
    assert is_transient(_ThrottledError("429"))
    assert not is_transient(_ThrottledError("404000"))
