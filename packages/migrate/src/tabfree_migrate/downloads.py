"""Download workbooks and data sources into content-addressed storage (BUILD_PLAN P1.4).

Order of work: every workbook and published data source **without** its extract first (small, and
enough for parsing and the census), then extracts only for the items selected. Each file streams
to a temporary file while it is hashed (sha256) and is stored once under
``blobs/sha256/<ab>/<sha>.<ext>`` in the sink (a local directory or the S3 raw zone).

``downloads/manifest.json`` in the same sink records, per ``<kind>/<luid>/<variant>``, the item's
``updated_at``, hash, size and storage key. Re-runs skip items whose ``updated_at`` is unchanged
and whose blob is still present, so an interrupted run simply continues. The manifest is saved
every few items and at the end, also when a run fails part-way.

Downloaded files are untrusted (CLAUDE.md rule 2): they are never opened, unzipped or executed
here. The file type is told from the first bytes only (``PK`` zip header → ``.twbx``/``.tdsx``).
TSC writes into a file object we own, so no server-supplied file name ever touches the disk.

**Unverified:** :class:`TscDownloader` has not been run against a live Tableau Server.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from tabfree_migrate.blobstore import BinaryWriter, HashingWriter, Sink, load_json, save_json, store_stream
from tabfree_migrate.inventory import InventoryStore
from tabfree_migrate.retry import with_retries

MANIFEST_KEY = "downloads/manifest.json"
KINDS = ("workbooks", "datasources")

_PACKAGED = {"workbooks": ".twbx", "datasources": ".tdsx"}
_PLAIN = {"workbooks": ".twb", "datasources": ".tds"}


@dataclass(frozen=True)
class DownloadTarget:
    kind: str
    luid: str
    name: str
    updated_at: str | None
    size: int | None = None


class ContentDownloader(Protocol):
    """Streams one workbook or data source into ``out``. TSC-backed; faked in tests."""

    def download(self, kind: str, luid: str, include_extract: bool, out: BinaryWriter) -> None: ...


def extension_for(kind: str, head: bytes) -> str:
    """``.twbx``/``.tdsx`` for zip packages, ``.twb``/``.tds`` for bare XML; decided from magic bytes."""
    return _PACKAGED[kind] if head.startswith(b"PK\x03\x04") else _PLAIN[kind]


def targets_from_inventory(store: InventoryStore, kinds: Iterable[str] = KINDS) -> list[DownloadTarget]:
    """Workbooks and data sources found by the P1.1 crawl, largest last."""
    targets = []
    for kind in kinds:
        if kind not in KINDS:
            raise ValueError(f"cannot download {kind!r}")
        for item in store.objects(kind):
            size = item.data.get("size")
            targets.append(
                DownloadTarget(kind, item.luid, item.name, item.updated_at, int(size) if size else None)
            )
    return sorted(targets, key=lambda t: (KINDS.index(t.kind), t.size or 0, t.name))


@dataclass
class DownloadStats:
    downloaded: int = 0
    skipped: int = 0
    bytes: int = 0
    errors: dict[str, str] = field(default_factory=dict)


ExtractPolicy = Callable[[DownloadTarget], bool]


def no_extracts(_target: DownloadTarget) -> bool:
    return False


def all_extracts(_target: DownloadTarget) -> bool:
    return True


def extracts_for(luids: Iterable[str]) -> ExtractPolicy:
    chosen = set(luids)
    return lambda target: target.luid in chosen


def _entry_key(target: DownloadTarget, include_extract: bool) -> str:
    variant = "with-extract" if include_extract else "no-extract"
    return f"{target.kind}/{target.luid}/{variant}"


def download_all(
    downloader: ContentDownloader,
    sink: Sink,
    targets: Iterable[DownloadTarget],
    extracts: ExtractPolicy = no_extracts,
    force: bool = False,
    save_every: int = 25,
    staging: Path | None = None,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadStats:
    """Download every target without extracts, then the selected ones with extracts.

    Errors are recorded per item (and in the manifest) and the run continues.
    """
    targets = list(targets)
    manifest = load_json(sink, MANIFEST_KEY)
    entries: dict[str, Any] = manifest.setdefault("entries", {})
    stats = DownloadStats()
    passes = [(t, False) for t in targets] + [(t, True) for t in targets if extracts(t)]
    pending = 0
    try:
        for target, include_extract in passes:
            key = _entry_key(target, include_extract)
            previous = entries.get(key)
            if (
                not force
                and previous
                and not previous.get("error")
                and previous.get("updated_at") == target.updated_at
                and target.updated_at is not None
                and sink.exists(str(previous.get("key")))
            ):
                stats.skipped += 1
                continue
            entry: dict[str, Any] = {
                "kind": target.kind,
                "luid": target.luid,
                "name": target.name,
                "updated_at": target.updated_at,
                "include_extract": include_extract,
                "downloaded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            try:
                fill = _filler(downloader, target, include_extract)
                ext = _ext_func(target.kind)
                # Each attempt streams into a fresh temporary file, so a retry never appends.
                ref = with_retries(
                    partial(store_stream, sink, fill, ext, staging),
                    attempts=attempts,
                    sleep=sleep,
                )
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
                stats.errors[key] = entry["error"]
            else:
                entry.update(sha256=ref.sha256, size=ref.size, key=ref.key, content_type=ref.content_type)
                stats.downloaded += 1
                stats.bytes += ref.size
            entries[key] = entry
            pending += 1
            if pending >= save_every:
                save_json(sink, MANIFEST_KEY, manifest)
                pending = 0
    finally:
        save_json(sink, MANIFEST_KEY, manifest)
    return stats


def _filler(
    downloader: ContentDownloader, target: DownloadTarget, include_extract: bool
) -> Callable[[HashingWriter], None]:
    def fill(writer: HashingWriter) -> None:
        downloader.download(target.kind, target.luid, include_extract, writer)

    return fill


def _ext_func(kind: str) -> Callable[[bytes], str]:
    return lambda head: extension_for(kind, head)


class TscDownloader:
    """:class:`ContentDownloader` over TSC's ``workbooks.download`` / ``datasources.download``.

    Read-only REST GETs; :func:`download_all` retries 429/5xx by restarting the whole stream.
    """

    def __init__(self, tsc_server: Any) -> None:
        self._srv = tsc_server

    def download(self, kind: str, luid: str, include_extract: bool, out: BinaryWriter) -> None:
        if kind not in KINDS:
            raise ValueError(f"cannot download {kind!r}")
        endpoint = getattr(self._srv, kind)
        endpoint.download(luid, filepath=out, include_extract=include_extract)
