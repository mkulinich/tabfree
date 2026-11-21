"""Content-addressed storage for files pulled off Tableau Server (BUILD_PLAN P1.4 and P1.5).

Blobs are stored once under ``blobs/sha256/<ab>/<sha256><ext>``, whatever their source, and small
JSON documents (manifests and indexes) are stored next to them under readable keys. Two sinks:

- :class:`LocalDirSink`: a directory on disk (tests, laptops, staging before upload).
- :class:`S3Sink`: the S3 raw zone, through boto3 from the optional ``aws`` extra. boto3 is imported
  only when no client is passed in, so tests use a fake client and never touch the network.

Downloaded Tableau files are untrusted: nothing here opens, unzips or executes them. They are
streamed to a temporary file while hashed, then handed to the sink as opaque bytes.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Buffer, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

CHUNK = 1024 * 1024

_CONTENT_TYPES = {
    ".png": "image/png",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
}


class BinaryWriter(Protocol):
    """Anything with ``write(bytes)``: what downloaders and exporters stream into."""

    def write(self, data: bytes, /) -> int: ...


class Sink(Protocol):
    """Where blobs and JSON documents go. Keys are ``/``-separated relative paths."""

    def exists(self, key: str) -> bool: ...

    def put_file(self, key: str, path: Path, content_type: str) -> None: ...

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes | None:
        """The stored bytes, or None when ``key`` does not exist."""
        ...

    def describe(self) -> str:
        """Where the sink writes, for messages (never includes credentials)."""
        ...


def _safe_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in {"", ".", ".."} for p in parts):
        raise ValueError(f"unsafe storage key {key!r}")
    return key


class LocalDirSink:
    """A :class:`Sink` rooted at a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*_safe_key(key).split("/"))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        shutil.copyfile(path, partial)
        os.replace(partial, target)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(data)
        os.replace(partial, target)

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def describe(self) -> str:
        return str(self.root)


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


class S3Sink:
    """A :class:`Sink` in an S3 bucket. Credentials come from the standard AWS provider chain.

    Not exercised against real S3 yet; tests use a fake client with the same four methods
    (``head_object``, ``upload_file``, ``put_object``, ``get_object``).
    """

    def __init__(self, bucket: str, prefix: str = "", client: Any = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            try:
                boto3: Any = importlib.import_module("boto3")
            except ImportError:
                raise RuntimeError("S3 output needs the optional extra: tabfree-migrate[aws]") from None
            client = boto3.client("s3")
        self._client = client

    @classmethod
    def from_url(cls, url: str, client: Any = None) -> S3Sink:
        if not url.startswith("s3://"):
            raise ValueError("S3 locations look like s3://bucket/prefix")
        bucket, _, prefix = url.removeprefix("s3://").partition("/")
        if not bucket:
            raise ValueError("S3 location has no bucket")
        return cls(bucket, prefix, client)

    def _key(self, key: str) -> str:
        key = _safe_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        # upload_file streams from disk and switches to multipart for large files.
        self._client.upload_file(
            str(path), self.bucket, self._key(key), ExtraArgs={"ContentType": content_type}
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise
        return bytes(response["Body"].read())

    def describe(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}" if self.prefix else f"s3://{self.bucket}"


def sink_for(location: str) -> Sink:
    """``s3://bucket/prefix`` → :class:`S3Sink`; anything else is a local directory."""
    if location.startswith("s3://"):
        return S3Sink.from_url(location)
    return LocalDirSink(Path(location))


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    size: int
    key: str
    content_type: str


def blob_key(sha256: str, extension: str) -> str:
    return f"blobs/sha256/{sha256[:2]}/{sha256}{extension}"


def content_type_for(extension: str) -> str:
    return _CONTENT_TYPES.get(extension, "application/octet-stream")


class HashingWriter(io.BufferedWriter):
    """A real ``io.BufferedWriter`` over the staging file that hashes and counts what it writes.

    It must be a ``BufferedWriter``: TSC's download only streams into objects that pass
    ``isinstance(f, (io.BytesIO, io.BufferedWriter))`` and treats anything else as a path.
    """

    def __init__(self, raw: io.FileIO) -> None:
        super().__init__(raw)
        self.digest = hashlib.sha256()
        self.size = 0
        self.head = b""

    def write(self, buffer: Buffer, /) -> int:
        data = bytes(buffer)
        if len(self.head) < 8:
            self.head = (self.head + data)[:8]
        self.digest.update(data)
        self.size += len(data)
        return super().write(data)


def store_stream(
    sink: Sink,
    fill: Callable[[HashingWriter], None],
    extension: str | Callable[[bytes], str],
    staging: Path | None = None,
) -> BlobRef:
    """Run ``fill(writer)`` to stream bytes to a temporary file, then store them content-addressed.

    ``extension`` may be a function of the first bytes (to tell ``.twbx`` from ``.twb`` without
    opening the file). The blob is uploaded only if the sink does not already hold it.
    """
    handle, name = tempfile.mkstemp(prefix="tabfree-", suffix=".part", dir=staging)
    tmp = Path(name)
    try:
        with HashingWriter(io.FileIO(handle, "wb", closefd=True)) as writer:
            fill(writer)
        sha = writer.digest.hexdigest()
        ext = extension(writer.head) if callable(extension) else extension
        key = blob_key(sha, ext)
        ctype = content_type_for(ext)
        if not sink.exists(key):
            sink.put_file(key, tmp, ctype)
        return BlobRef(sha, writer.size, key, ctype)
    finally:
        tmp.unlink(missing_ok=True)


def store_chunks(sink: Sink, chunks: Iterable[bytes], extension: str, staging: Path | None = None) -> BlobRef:
    def fill(writer: HashingWriter) -> None:
        for chunk in chunks:
            writer.write(chunk)

    return store_stream(sink, fill, extension, staging)


def load_json(sink: Sink, key: str) -> dict[str, Any]:
    data = sink.get_bytes(key)
    if data is None:
        return {}
    loaded = json.loads(data)
    if not isinstance(loaded, dict):
        raise ValueError(f"{key} is not a JSON object")
    return loaded


def save_json(sink: Sink, key: str, document: dict[str, Any]) -> None:
    payload = json.dumps(document, indent=2, sort_keys=True, default=str).encode()
    sink.put_bytes(key, payload, "application/json")
