"""Test doubles for code that talks to Tableau. Used by this package's tests and by later phases."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tabfree_migrate.blobstore import BinaryWriter
from tabfree_migrate.ground_truth import ExportOptions
from tabfree_migrate.tableau import ServerInfo

DEFAULT_COUNTS: dict[str, int | None] = {
    "projects": 12,
    "workbooks": 340,
    "views": 2100,
    "datasources": 85,
    "flows": 0,
    "users": 450,
    "groups": 31,
}


class FakeReader:
    """In-memory stand-in for a signed-in Tableau session (implements ``TableauReader``)."""

    def __init__(
        self,
        counts: dict[str, int | None] | None = None,
        sites: int | None = 2,
        metadata: bool = True,
        info: ServerInfo | None = None,
    ) -> None:
        self.counts = DEFAULT_COUNTS if counts is None else counts
        self.sites = sites
        self.metadata = metadata
        self.info = info or ServerInfo("2026.3.0", "20263.26.1015.0800", "3.30")

    def server_info(self) -> ServerInfo:
        return self.info

    def count(self, endpoint: str) -> int | None:
        return self.counts.get(endpoint)

    def site_count(self) -> int | None:
        return self.sites

    def metadata_api_available(self) -> bool:
        return self.metadata

    def sample_names(self, endpoint: str, limit: int) -> list[str]:
        return [f"{endpoint}-{i}" for i in range(1, limit + 1)]


class FakeMetadataClient:
    """In-memory Metadata API (implements ``MetadataClient``) with cursor paging.

    ``nodes`` maps a connection name (``workbooksConnection``) to its nodes. A page larger than
    ``max_page`` is answered with Tableau's node-limit error; connections in ``broken`` answer
    with a GraphQL error; after ``fail_after`` calls every call raises (a dropped connection).
    """

    server = "https://tableau.example.com"
    site = "finance"

    def __init__(
        self,
        nodes: dict[str, list[dict[str, Any]]] | None = None,
        max_page: int | None = None,
        broken: set[str] | None = None,
    ) -> None:
        self.nodes = nodes or {}
        self.max_page = max_page
        self.broken = broken or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_after: int | None = None

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        match = re.search(r"(\w+Connection)\(", query)
        if match is None:
            raise ValueError("not a connection query")
        connection = match.group(1)
        self.calls.append((connection, dict(variables)))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise ConnectionError("connection reset")
        if connection in self.broken:
            return {"errors": [{"message": f"Cannot query field on {connection}"}]}
        first = int(variables["first"])
        if self.max_page is not None and first > self.max_page:
            return {
                "data": None,
                "errors": [
                    {
                        "message": "Showing partial results. The request exceeded the 20000 node limit.",
                        "extensions": {"code": "NODE_LIMIT_EXCEEDED"},
                    }
                ],
            }
        everything = self.nodes.get(connection, [])
        start = int(variables.get("afterToken") or 0)
        page = everything[start : start + first]
        end = start + len(page)
        return {
            "data": {
                connection: {
                    "nodes": page,
                    "pageInfo": {"hasNextPage": end < len(everything), "endCursor": str(end)},
                    "totalCount": len(everything),
                }
            }
        }


class FakeDownloader:
    """Implements ``ContentDownloader``: serves bytes per ``(kind, luid, include_extract)``.

    ``failures[key]`` is a queue of exceptions raised (after writing a few bytes) before success.
    """

    def __init__(self, files: dict[tuple[str, str, bool], bytes]) -> None:
        self.files = files
        self.calls: list[tuple[str, str, bool]] = []
        self.failures: dict[tuple[str, str, bool], list[Exception]] = {}

    def download(self, kind: str, luid: str, include_extract: bool, out: BinaryWriter) -> None:
        key = (kind, luid, include_extract)
        self.calls.append(key)
        queued = self.failures.get(key)
        if queued:
            out.write(b"partial")
            raise queued.pop(0)
        data = self.files[key]
        for i in range(0, len(data), 4):
            out.write(data[i : i + 4])


class FakeViewExporter:
    """Implements ``ViewExporter``: deterministic bytes derived from the request."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.broken: set[tuple[str, str]] = set()

    def export(self, view_luid: str, fmt: str, options: ExportOptions, out: BinaryWriter) -> None:
        params = options.query_params(fmt)
        self.calls.append((view_luid, fmt, params))
        if (view_luid, fmt) in self.broken:
            raise PermissionError(f"403 exporting {fmt}")
        out.write(f"{fmt}:{view_luid}:{sorted(params.items())}".encode())


class FakeS3Error(Exception):
    """Shaped like botocore's ``ClientError`` (only ``response["Error"]["Code"]``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """The four boto3 S3 client methods ``S3Sink`` uses, backed by a dict. No network."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 - boto3 names
        if (Bucket, Key) not in self.objects:
            raise FakeS3Error("404")
        return {"ContentLength": len(self.objects[(Bucket, Key)][0])}

    def upload_file(self, Filename: str, Bucket: str, Key: str, ExtraArgs: dict[str, str]) -> None:  # noqa: N803
        self.objects[(Bucket, Key)] = (Path(Filename).read_bytes(), ExtraArgs["ContentType"])

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise FakeS3Error("NoSuchKey")
        return {"Body": _Body(self.objects[(Bucket, Key)][0])}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise FakeS3Error("404")
        Path(Filename).write_bytes(self.objects[(Bucket, Key)][0])


def fake_convert_extract(path: Path, out_dir: Path, *, table_prefix: str = "") -> list[Any]:
    """Stand-in for ``hyper.convert_hyper`` (module-level, so a process pool can pickle it).

    Writes a placeholder Parquet file and returns fixed metadata. A file whose first bytes
    contain ``FAIL`` raises, to exercise error handling.
    """
    from tabfree_migrate.hyper import ColumnInfo, TableResult

    with path.open("rb") as handle:
        if b"FAIL" in handle.read(4096):
            raise RuntimeError("fake conversion failure")
    out_dir.mkdir(parents=True, exist_ok=True)
    glue_table = f"{table_prefix}__extract" if table_prefix else "extract"
    target = out_dir / f"{glue_table}.parquet"
    target.write_bytes(b"PAR1fakePAR1")
    columns = [
        ColumnInfo("Order Date", "order_date", "DATE", "date", True),
        ColumnInfo("Sales", "sales", "DOUBLE", "double", True),
        ColumnInfo("Location", "location", "GEOGRAPHY", "string", True),
    ]
    stats = {"rows": 2, "columns": {"order_date": {"count": 2, "nulls": 0}}}
    return [TableResult("Extract", "Extract", glue_table, 2, columns, str(target), stats)]
