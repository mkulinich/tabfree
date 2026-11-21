"""Convert many extracts in parallel, resumably (BUILD_PLAN P2.8, ``tabfree-migrate convert-batch``).

Inputs are either a local directory (``.hyper``, ``.tde``, ``.twbx``, ``.tdsx``) or the
content-addressed store written by ``download`` (``downloads/manifest.json``, local or S3; only
entries downloaded with their extract). Packages are opened as zip files and their extract
members copied out; nothing inside them is executed (CLAUDE.md rule 2). The IR of a package is
parsed only to read its extract settings.

Every extract is identified by the sha256 of its bytes. Output goes to a sink (local directory
or ``s3://``) under ``extracts/<sha256>/``: one ``<glue_table>/data.parquet`` per table and a
``report.json`` with types, Hyper-side statistics, the table definition, Athena DDL and the Athena
parity query. ``extracts/manifest.json`` (one per shard with ``--shard``) records each source and
extract, so a re-run skips everything already converted, and parallel Fargate tasks can split
the work with ``--shard INDEX/COUNT``. Within one task a process pool converts extracts in
parallel; each worker starts its own ``hyperd``.

Legacy ``.tde`` extracts are reported as ``needs-upgrade`` (see :mod:`tabfree_migrate.tde`). With
``--tde upgrade`` in the legacy image they are upgraded to ``upgraded/<sha256>.hyper`` in the
output; a later run in the normal image finds that file and converts it.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import shutil
import tempfile
import time
import traceback
import zipfile
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from tabfree_migrate import __version__
from tabfree_migrate.blobstore import LocalDirSink, S3Sink, Sink, load_json, save_json, sink_for
from tabfree_migrate.extract_format import ExtractFormat, sniff
from tabfree_migrate.extract_settings import (
    ExtractSettings,
    glue_parameters,
    match_settings,
    settings_from_ir,
    table_definition,
)
from tabfree_migrate.hyper import (
    TableResult,
    athena_ddl,
    athena_parity_query,
    glue_identifier,
    iceberg_table_ddl,
)
from tabfree_migrate.parity import (
    Dialect,
    ParityResult,
    QueryRunner,
    athena_table,
    compare_stats,
    measure,
)
from tabfree_migrate.tde import tde_reader_available, upgrade_advice, upgrade_tde

REPORT_SCHEMA = "tabfree.extract-conversion/v1"
DOWNLOADS_MANIFEST = "downloads/manifest.json"
EXTRACT_SUFFIXES = {".hyper", ".tde"}
PACKAGE_SUFFIXES = {".twbx", ".tdsx"}
#: Extract members larger than this are refused (zip bombs); extracts above 1 TiB are not expected.
MAX_EXTRACT_BYTES = 1 << 40
CHUNK = 1 << 20

Converter = Callable[..., list[TableResult]]
TdeMode = Literal["flag", "upgrade"]


def _default_converter(path: Path, out_dir: Path, *, table_prefix: str = "") -> list[TableResult]:
    from tabfree_migrate.hyper import convert_hyper

    return convert_hyper(path, out_dir, table_prefix=table_prefix)


@dataclass(frozen=True)
class BatchOptions:
    out: str
    database: str = "tabfree_lake"
    iceberg_root: str | None = None
    tde_mode: TdeMode = "flag"
    work_dir: str = ""
    converter: Converter = _default_converter
    #: Opens the output location; module-level so workers can unpickle it (tests inject fakes).
    sink_factory: Callable[[str], Sink] = sink_for


@dataclass(frozen=True)
class ExtractSource:
    """A file to look at: a bare extract or a package. ``source_id`` is its sha256."""

    source_id: str
    origin: str
    name: str
    path: str | None = None
    store_key: str | None = None


@dataclass(frozen=True)
class ExtractJob:
    sha256: str
    path: str
    origin: str
    name: str
    format: str
    member: str | None = None
    settings: dict[str, Any] | None = None
    staged: bool = False


@dataclass
class BatchStats:
    converted: int = 0
    skipped: int = 0
    flagged: int = 0
    upgraded: int = 0
    failed: int = 0
    reports: list[dict[str, Any]] = field(default_factory=list)


def report_key(sha256: str) -> str:
    return f"extracts/{sha256}/report.json"


def upgraded_key(sha256: str) -> str:
    return f"upgraded/{sha256}.hyper"


def manifest_key(shard: tuple[int, int]) -> str:
    index, count = shard
    return "extracts/manifest.json" if count == 1 else f"extracts/manifest.shard-{index}-of-{count}.json"


def parse_shard(text: str) -> tuple[int, int]:
    """``"2/8"`` → ``(2, 8)``; index is zero-based."""
    index_text, _, count_text = text.partition("/")
    index, count = int(index_text), int(count_text or 1)
    if count < 1 or not 0 <= index < count:
        raise ValueError(f"shard must be INDEX/COUNT with 0 <= INDEX < COUNT, got {text!r}")
    return index, count


def in_shard(source_id: str, shard: tuple[int, int]) -> bool:
    index, count = shard
    return int(source_id[:15], 16) % count == index


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# --- discovery -----------------------------------------------------------------------------------


def sources_from_directory(root: Path) -> list[ExtractSource]:
    """Extracts and packages under ``root``, by suffix (their content is sniffed later)."""
    sources = []
    for path in sorted(root.rglob("*")):
        suffix = path.suffix.lower()
        if path.is_file() and not path.is_symlink() and suffix in EXTRACT_SUFFIXES | PACKAGE_SUFFIXES:
            sources.append(ExtractSource(sha256_file(path), str(path), path.name, path=str(path)))
    return sources


def sources_from_store(sink: Sink) -> list[ExtractSource]:
    """Packages downloaded with their extracts, from the ``download`` manifest."""
    manifest = load_json(sink, DOWNLOADS_MANIFEST)
    sources = []
    for entry_id, entry in sorted(dict(manifest.get("entries", {})).items()):
        if not entry.get("include_extract") or entry.get("error") or not entry.get("key"):
            continue
        sha, key = str(entry["sha256"]), str(entry["key"])
        name = f"{entry.get('name') or entry_id}{PurePosixPath(key).suffix}"
        sources.append(ExtractSource(sha, f"{sink.describe()}/{key}", name, store_key=key))
    return sources


def fetch(sink: Sink, key: str, target: Path) -> Path:
    """A local path for a stored blob: the file itself for a local store, else a download."""
    if isinstance(sink, LocalDirSink):
        return sink.root.joinpath(*key.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(sink, S3Sink):
        sink._client.download_file(sink.bucket, sink._key(key), str(target))
        return target
    data = sink.get_bytes(key)
    if data is None:
        raise FileNotFoundError(key)
    target.write_bytes(data)
    return target


def _package_settings(path: Path) -> list[ExtractSettings]:
    try:
        from tabfree_migrate.twb import parse_file

        return settings_from_ir(parse_file(path))
    except Exception:  # the IR is a bonus here; a parse failure is reported per extract
        return []


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, staging: Path) -> tuple[str, Path]:
    if info.file_size > MAX_EXTRACT_BYTES:
        raise ValueError(f"{info.filename}: {info.file_size} bytes exceeds the extract size limit")
    handle, name = tempfile.mkstemp(prefix="tabfree-member-", dir=staging)
    digest = hashlib.sha256()
    written = 0
    with archive.open(info) as src, os.fdopen(handle, "wb") as dst:
        while chunk := src.read(CHUNK):
            written += len(chunk)
            if written > MAX_EXTRACT_BYTES:
                raise ValueError(f"{info.filename}: larger than declared")
            digest.update(chunk)
            dst.write(chunk)
    sha = digest.hexdigest()
    # The member's own name never reaches the file system: only its hash and suffix.
    target = staging / f"{sha}{PurePosixPath(info.filename).suffix.lower()}"
    os.replace(name, target)
    return sha, target


def expand(source: ExtractSource, path: Path, staging: Path) -> Iterator[ExtractJob]:
    """The extracts in one source: itself, or each extract member of a package."""
    fmt = sniff(path)
    if fmt is not ExtractFormat.PACKAGE:
        yield ExtractJob(source.source_id, str(path), source.origin, source.name, fmt.value)
        return
    settings = _package_settings(path)
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or PurePosixPath(info.filename).suffix.lower() not in EXTRACT_SUFFIXES:
                continue
            sha, staged = _copy_member(archive, info, staging)
            matched = match_settings(settings, info.filename)
            yield ExtractJob(
                sha,
                str(staged),
                source.origin,
                source.name,
                sniff(staged).value,
                member=info.filename,
                settings=matched.to_dict() if matched else None,
                staged=True,
            )


# --- one extract (runs in a worker process) ------------------------------------------------------


def _location(out: str, key_prefix: str) -> str:
    return f"{out.rstrip('/')}/{key_prefix}" if out.startswith("s3://") else str(Path(out) / key_prefix)


def _table_prefix(job: ExtractJob) -> str:
    stem = PurePosixPath(job.member or job.name).stem
    return f"{glue_identifier(stem)[:40]}_{job.sha256[:8]}"


def _table_report(
    job: ExtractJob, result: TableResult, options: BatchOptions, sink: Sink, warnings: list[str]
) -> dict[str, Any]:
    prefix = f"extracts/{job.sha256}/{result.glue_table}/"
    parquet_key = f"{prefix}data.parquet"
    sink.put_file(parquet_key, Path(result.parquet), "application/vnd.apache.parquet")
    staging_location = _location(options.out, prefix)
    if not options.out.startswith("s3://"):
        warnings.append("local output: point the staging LOCATION at the uploaded S3 prefix")
    root = options.iceberg_root or (
        _location(options.out, f"warehouse/{options.database}")
        if options.out.startswith("s3://")
        else f"s3://<lake-bucket>/warehouse/{options.database}"
    )
    iceberg_location = f"{root.rstrip('/')}/{result.glue_table}/"
    settings = ExtractSettings(**job.settings) if job.settings else None
    source = {
        "sha256": job.sha256,
        "origin": job.origin,
        "member": job.member,
        "schema": result.schema,
        "table": result.table,
    }
    definition = table_definition(settings, result.columns, source=source)
    definition["iceberg_ddl"] = iceberg_table_ddl(
        result, database=options.database, location=iceberg_location
    )
    params = glue_parameters(definition)
    params["tabfree.converter"] = f"tabfree-migrate {__version__}"
    table = asdict(result)
    table["parquet"] = parquet_key
    table.update(
        staging_location=staging_location,
        iceberg_location=iceberg_location,
        definition=definition,
        glue_parameters=params,
        athena_ddl=athena_ddl(
            result,
            database=options.database,
            staging_location=staging_location,
            iceberg_location=iceberg_location,
            table_properties=params,
        ),
        athena_parity_sql=athena_parity_query(result, database=options.database).sql,
        type_notes={c.glue_name: c.notes for c in result.columns if c.notes},
    )
    return table


def _convert(job: ExtractJob, path: Path, options: BatchOptions, sink: Sink, work: Path) -> dict[str, Any]:
    warnings: list[str] = []
    out_dir = work / "parquet"
    results = options.converter(path, out_dir, table_prefix=_table_prefix(job))
    tables = [_table_report(job, r, options, sink, warnings) for r in results]
    if job.settings is None:
        warnings.append("no extract settings found in the IR (bare extract or unmatched package member)")
    return {"status": "converted", "tables": tables, "warnings": sorted(set(warnings))}


def _handle_tde(job: ExtractJob, options: BatchOptions, sink: Sink, work: Path) -> dict[str, Any]:
    key = upgraded_key(job.sha256)
    if sink.exists(key):
        upgraded = fetch(sink, key, work / "upgraded.hyper")
        result = _convert(job, upgraded, options, sink, work)
        result["upgraded_from_tde"] = key
        return result
    if options.tde_mode == "upgrade" and tde_reader_available():
        target = work / "upgraded.hyper"
        tables = upgrade_tde(Path(job.path), target, log_dir=work)
        sink.put_file(key, target, "application/octet-stream")
        return {"status": "upgraded", "upgraded_key": key, "upgraded_tables": tables, "tables": []}
    advice = upgrade_advice()
    if options.tde_mode == "upgrade":
        advice["note"] = "--tde upgrade needs the legacy image; this one has no .tde reader"
    return {"status": "needs-upgrade", "advice": advice, "tables": []}


def run_job(job: ExtractJob, options: BatchOptions) -> dict[str, Any]:
    """Convert one extract, upload its output and report; never raises."""
    started = time.monotonic()
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "sha256": job.sha256,
        "origin": job.origin,
        "name": job.name,
        "member": job.member,
        "format": job.format,
        "settings": job.settings,
        "converter": f"tabfree-migrate {__version__}",
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    work = Path(options.work_dir or tempfile.gettempdir()) / f"job-{job.sha256}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        sink = options.sink_factory(options.out)
        if job.format == ExtractFormat.HYPER.value:
            report.update(_convert(job, Path(job.path), options, sink, work))
        elif job.format == ExtractFormat.TDE.value:
            report.update(_handle_tde(job, options, sink, work))
        else:
            report.update(status="unsupported", error=f"not a Hyper or TDE extract ({job.format})", tables=[])
        report["duration_s"] = round(time.monotonic() - started, 3)
        save_json(sink, report_key(job.sha256), report)
    except Exception as exc:
        report.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(limit=5),
            duration_s=round(time.monotonic() - started, 3),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


# --- the batch -----------------------------------------------------------------------------------


def _done_statuses(tde_mode: TdeMode) -> set[str]:
    # "needs-upgrade" is final unless this run can upgrade; "upgraded" is final only in the legacy
    # image (the normal image converts the upgraded .hyper).
    return {"converted", "unsupported"} | ({"upgraded"} if tde_mode == "upgrade" else {"needs-upgrade"})


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "origin": report.get("origin"),
        "member": report.get("member"),
        "error": report.get("error"),
        "report": report_key(str(report["sha256"])),
        "tables": [t.get("glue_table") for t in report.get("tables", [])],
        "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def convert_batch(
    sources: Iterable[ExtractSource],
    options: BatchOptions,
    *,
    store: Sink | None = None,
    workers: int = 1,
    shard: tuple[int, int] = (0, 1),
    force: bool = False,
    on_report: Callable[[dict[str, Any]], None] | None = None,
) -> BatchStats:
    """Convert every extract in ``sources`` that is not done yet; see the module docstring."""
    sink = options.sink_factory(options.out)
    key = manifest_key(shard)
    manifest = load_json(sink, key)
    entries: dict[str, Any] = manifest.setdefault("entries", {})
    seen_sources: dict[str, Any] = manifest.setdefault("sources", {})
    done = _done_statuses(options.tde_mode)
    stats = BatchStats()
    work_root = Path(options.work_dir or tempfile.mkdtemp(prefix="tabfree-batch-"))
    staging = work_root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    options = replace(options, work_dir=str(work_root))

    def finished(sha: str) -> bool:
        entry = entries.get(sha)
        if entry is None or entry.get("status") not in done or not sink.exists(report_key(sha)):
            return False
        # A .tde flagged earlier becomes convertible once the legacy image has upgraded it.
        return not (entry.get("status") == "needs-upgrade" and sink.exists(upgraded_key(sha)))

    def record(report: dict[str, Any], job: ExtractJob) -> None:
        entries[job.sha256] = _summary(report)
        status = report.get("status")
        if status == "converted":
            stats.converted += 1
        elif status == "needs-upgrade":
            stats.flagged += 1
        elif status == "upgraded":
            stats.upgraded += 1
        else:
            stats.failed += 1
        stats.reports.append(report)
        if job.staged:
            Path(job.path).unlink(missing_ok=True)
        save_json(sink, key, manifest)
        if on_report:
            on_report(report)

    pool = (
        ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
        if workers > 1
        else None
    )
    pending: dict[Future[dict[str, Any]], ExtractJob] = {}

    def drain(limit: int) -> None:
        """Record finished jobs until at most ``limit`` are pending (bounds staged disk use)."""
        while len(pending) > limit:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                job = pending.pop(future)
                try:
                    report = future.result()
                except Exception as exc:  # the worker process itself died
                    report = {
                        "sha256": job.sha256,
                        "origin": job.origin,
                        "status": "failed",
                        "error": str(exc),
                    }
                record(report, job)

    try:
        for source in sources:
            if not in_shard(source.source_id, shard):
                continue
            known = seen_sources.get(source.source_id)
            if (
                not force
                and known is not None
                and "extracts" in known
                and all(finished(s) for s in known["extracts"])
            ):
                stats.skipped += max(1, len(known.get("extracts", [])))
                continue
            shas: list[str] = []
            try:
                if source.path is not None:
                    path = Path(source.path)
                else:
                    if store is None or source.store_key is None:
                        raise ValueError(f"{source.origin}: no path and no store")
                    path = fetch(store, source.store_key, staging / f"source-{source.source_id}")
                for job in expand(source, path, staging):
                    shas.append(job.sha256)
                    if not force and finished(job.sha256):
                        stats.skipped += 1
                        if job.staged:
                            Path(job.path).unlink(missing_ok=True)
                        continue
                    if pool is None:
                        record(run_job(job, options), job)
                    else:
                        pending[pool.submit(run_job, job, options)] = job
                        drain(2 * workers)
                if path.parent == staging and not any(j.path == str(path) for j in pending.values()):
                    path.unlink(missing_ok=True)  # a downloaded package, members already copied out
            except Exception as exc:
                stats.failed += 1
                seen_sources[source.source_id] = {
                    "origin": source.origin,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                save_json(sink, key, manifest)
                continue
            seen_sources[source.source_id] = {"origin": source.origin, "extracts": shas}
        drain(0)
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        save_json(sink, key, manifest)
        shutil.rmtree(staging, ignore_errors=True)
    return stats


def check_report_parity(
    report: dict[str, Any], runner: QueryRunner, dialect: Dialect, *, database: str | None = None
) -> dict[str, ParityResult]:
    """Compare every table of a conversion report with its copy, through ``runner``.

    ``athena`` queries ``"<database>"."<glue_table>"``; ``duckdb`` queries a view named after the
    Glue table (see :func:`tabfree_migrate.parity.duckdb_parquet_runner`).
    """
    results = {}
    for data in report.get("tables", []):
        table = TableResult.from_dict(data)
        if dialect == "athena":
            table_sql = athena_table(database or "tabfree_lake", table.glue_table)
        else:
            table_sql = '"' + table.glue_table.replace('"', '""') + '"'
        actual = measure(runner, table_sql, table.columns, dialect)
        results[table.glue_table] = compare_stats(table.hyper_stats(), actual, table.columns)
    return results
