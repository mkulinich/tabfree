"""``tabfree-migrate`` command line. Every command is read-only against Tableau."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn, cast

import typer

from tabfree_migrate import __version__
from tabfree_migrate.corpus import verify_corpus
from tabfree_migrate.repository import RepositoryConnection
from tabfree_migrate.smoke import format_report, run_smoke
from tabfree_migrate.tableau import (
    ConfigError,
    TableauAccessError,
    TableauConnection,
    TableauReader,
    TscReader,
)

if TYPE_CHECKING:
    from tabfree_migrate.downloads import ContentDownloader
    from tabfree_migrate.ground_truth import ViewExporter
    from tabfree_migrate.metadata import MetadataClient
    from tabfree_migrate.repository import RepositoryReader

app = typer.Typer(
    help="TabFree migration toolkit. Every command is read-only against Tableau.",
    no_args_is_help=True,
    add_completion=False,
)


@contextmanager
def open_reader(connection: TableauConnection) -> Iterator[TableauReader]:
    """Sign in, yield a reader, and always sign out (replaced by a fake in tests)."""
    with TscReader(connection) as reader:
        yield reader


@app.command()
def version() -> None:
    """Print the toolkit version."""
    typer.echo(f"tabfree-migrate {__version__}")


@app.command()
def smoke(
    sample: Annotated[
        int, typer.Option(min=0, max=100, help="Also list up to N project and workbook names.")
    ] = 0,
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Sign in with a personal access token and report what it can see.

    Reads TABLEAU_SERVER_URL, TABLEAU_SITE, TABLEAU_PAT_NAME and TABLEAU_PAT_SECRET from the
    environment. The secret is never printed.
    """
    try:
        connection = TableauConnection.from_env(os.environ)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    try:
        with open_reader(connection) as reader:
            report = run_smoke(reader, connection, sample)
    except (TableauAccessError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(report.to_dict(), indent=2) if as_json else format_report(report))


@app.command("corpus-check")
def corpus_check(
    root: Annotated[Path, typer.Option(help="Repository root that contains corpus/manifest.json.")] = Path(
        "."
    ),
) -> None:
    """Verify corpus files against corpus/manifest.json: presence, hashes and XML structure."""
    report = verify_corpus(root)
    for problem in report.problems:
        typer.echo(f"FAIL {problem}")
    typer.echo(f"{report.checked} checked, {report.skipped} external, {len(report.problems)} problems")
    if report.problems:
        raise typer.Exit(code=1)


def _connection_or_exit() -> TableauConnection:
    try:
        return TableauConnection.from_env(os.environ)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None


@app.command()
def crawl(
    db: Annotated[Path, typer.Option(help="SQLite inventory file (created if missing).")] = Path(
        "inventory.db"
    ),
    types: Annotated[
        list[str] | None, typer.Option("--type", help="Object types to crawl (default: all).")
    ] = None,
) -> None:
    """Inventory a Tableau site into SQLite: users, groups, projects, content, permissions, tasks."""
    from tabfree_migrate.inventory import OBJECT_TYPES, InventoryStore
    from tabfree_migrate.inventory import crawl as run_crawl
    from tabfree_migrate.inventory_tsc import TscInventorySource

    connection = _connection_or_exit()
    store = InventoryStore(db)
    try:
        with TscReader(connection) as reader:
            stats = run_crawl(TscInventorySource(connection, reader), store, types or OBJECT_TYPES)
    except (TableauAccessError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        store.close()
    for object_type, count in stats.counts.items():
        note = f"  ERROR {stats.errors[object_type]}" if object_type in stats.errors else ""
        typer.echo(f"{object_type:<14} {count}{note}")
    typer.echo(f"added {stats.added}, updated {stats.updated}, unchanged {stats.unchanged}")


@app.command()
def parse(
    paths: Annotated[list[Path], typer.Argument(help=".twb/.twbx/.tds/.tdsx files")],
    out: Annotated[Path, typer.Option(help="Directory for <name>.ir.json files.")] = Path("ir"),
) -> None:
    """Parse Tableau files into the TabFree IR and validate each one against its schema."""
    from tabfree_migrate.twb import TableauFileError, parse_file
    from tabfree_schemas import SchemaKind, schema_errors

    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for path in paths:
        try:
            ir = parse_file(path)
        except TableauFileError as exc:
            failed += 1
            typer.echo(f"FAIL {path}: {exc}")
            continue
        errors = schema_errors(SchemaKind.TABLEAU_IR, ir)
        target = out / f"{path.stem}.ir.json"
        target.write_text(json.dumps(ir, indent=2), encoding="utf-8")
        if errors:
            failed += 1
            typer.echo(f"FAIL {path}: {errors[0]}")
        else:
            typer.echo(f"ok   {path} -> {target}")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def census(
    paths: Annotated[list[Path], typer.Argument(help=".twb/.twbx/.tds/.tdsx files")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Count constructs and score migration complexity per workbook and for the whole set."""
    from tabfree_migrate.census import census_of, estate_census
    from tabfree_migrate.twb import TableauFileError, parse_file

    per_file: dict[str, dict[str, object]] = {}
    irs = []
    for path in paths:
        try:
            ir = parse_file(path)
        except TableauFileError as exc:
            typer.echo(f"skip {path}: {exc}", err=True)
            continue
        irs.append(ir)
        per_file[str(path)] = census_of(ir).to_dict()
    total = estate_census(irs).to_dict()
    if as_json:
        typer.echo(json.dumps({"files": per_file, "estate": total}, indent=2))
        return
    ranked = sorted(per_file.items(), key=lambda kv: float(str(kv[1]["score"])), reverse=True)
    for name, info in ranked:
        typer.echo(f"{info['score']:>8}  {name}")
    typer.echo("\nEstate totals:")
    totals = cast("dict[str, int]", total["totals"])
    for key, value in sorted(totals.items()):
        typer.echo(f"  {key:<20} {value}")


@app.command("convert-hyper")
def convert_hyper_command(
    path: Annotated[Path, typer.Argument(help=".hyper extract")],
    out: Annotated[Path, typer.Option(help="Output directory for Parquet files.")] = Path("parquet"),
) -> None:
    """Export every table of a .hyper extract to Parquet with parity statistics (needs \\[hyper])."""
    try:
        from tabfree_migrate.hyper import convert_hyper, write_report
    except ImportError as exc:  # pragma: no cover - depends on the platform
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    try:
        results = convert_hyper(path, out)
    except ImportError:
        typer.echo("error: install tabfree-migrate[hyper] (Linux x86-64 or Windows)", err=True)
        raise typer.Exit(code=2) from None
    write_report(results, out / "conversion.json")
    for result in results:
        typer.echo(f"{result.glue_table:<30} {result.rows:>10} rows -> {result.parquet}")


@app.command()
def report(
    paths: Annotated[list[Path], typer.Argument(help=".twb/.twbx files (or directories to search)")],
    out: Annotated[Path, typer.Option(help="Directory for report.md and report.json.")] = Path("report"),
    inventory: Annotated[Path | None, typer.Option(help="The crawler's inventory database.")] = None,
    usage: Annotated[Path | None, typer.Option(help="CSV with workbook,views_180d columns.")] = None,
    repository: Annotated[
        Path | None, typer.Option(help="The repository harvest database (usage from views_stats).")
    ] = None,
) -> None:
    """Inventory report (P1.6): census, connections, RLS, compiler readiness and triage."""
    from tabfree_migrate import report as rpt
    from tabfree_migrate.twb import TableauFileError, parse_file

    files: list[Path] = []
    for path in paths:
        files += (
            sorted(p for p in path.rglob("*") if p.suffix.lower() in (".twb", ".twbx"))
            if path.is_dir()
            else [path]
        )
    irs = []
    for path in files:
        try:
            irs.append((str(path), parse_file(path)))
        except TableauFileError as exc:
            typer.echo(f"skip {path}: {exc}")
    workbooks, connections, estate = rpt.build(
        irs,
        usage=rpt.load_usage(usage) if usage else rpt.repository_usage(repository) if repository else None,
        inventory=rpt.inventory_details(inventory) if inventory else None,
    )
    md, js = rpt.write_report(out, workbooks, connections, estate)
    typer.echo(f"{len(workbooks)} workbooks -> {md} and {js}")


# --- P1.2-P1.5: harvests, downloads and ground truth ------------------------------------------
# These factories are the only places that open real connections; tests replace them.


@contextmanager
def metadata_client(connection: TableauConnection) -> Iterator[MetadataClient]:
    from tabfree_migrate.metadata import TscMetadataClient

    with TscReader(connection) as reader:
        yield TscMetadataClient(connection.server_url, connection.site, reader._server)


@contextmanager
def repository_reader(connection: RepositoryConnection, timeout_ms: int) -> Iterator[RepositoryReader]:
    from tabfree_migrate.repository import PsycopgRepository

    with PsycopgRepository(connection, timeout_ms) as reader:
        yield reader


@contextmanager
def content_downloader(connection: TableauConnection) -> Iterator[ContentDownloader]:
    from tabfree_migrate.downloads import TscDownloader

    with TscReader(connection) as reader:
        yield TscDownloader(reader._server)


@contextmanager
def view_exporter(connection: TableauConnection) -> Iterator[ViewExporter]:
    from tabfree_migrate.ground_truth import TscViewExporter

    with TscReader(connection) as reader:
        yield TscViewExporter(reader._server, reader._tsc)


def _fail(exc: BaseException, code: int = 1) -> NoReturn:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=code) from None


_FAILURES = (TableauAccessError, OSError, RuntimeError)


@app.command("harvest-metadata")
def harvest_metadata(
    db: Annotated[Path, typer.Option(help="SQLite file (shared with the inventory).")] = Path("inventory.db"),
    entities: Annotated[
        list[str] | None, typer.Option("--entity", help="Metadata API entities (default: all).")
    ] = None,
    resume: Annotated[bool, typer.Option(help="Continue an interrupted harvest from its cursors.")] = False,
    page_size: Annotated[
        int | None, typer.Option(min=1, max=1000, help="Override the per-entity page size.")
    ] = None,
) -> None:
    """Harvest lineage, formulas, custom SQL and RLS flags from the Metadata API (read-only)."""
    from tabfree_migrate.metadata import ENTITY_NAMES, MetadataStore, harvest

    connection = _connection_or_exit()
    store = MetadataStore(db)
    try:
        with metadata_client(connection) as client:
            stats = harvest(client, store, entities or ENTITY_NAMES, resume=resume, page_size=page_size)
    except ValueError as exc:
        _fail(exc, code=2)
    except _FAILURES as exc:
        _fail(exc)
    finally:
        store.close()
    for name, entity in stats.entities.items():
        note = f"  ERROR {entity.error}" if entity.error else ""
        total = "" if entity.total is None else f" of {entity.total}"
        typer.echo(f"{name:<22} {entity.count}{total} ({entity.pages} pages){note}")


@app.command("harvest-repository")
def harvest_repository_command(
    db: Annotated[Path, typer.Option(help="SQLite file (shared with the inventory).")] = Path("inventory.db"),
    sources: Annotated[
        list[str] | None, typer.Option("--source", help="Repository sources (default: all).")
    ] = None,
    since_days: Annotated[int, typer.Option(min=1, help="History window for usage tables.")] = 183,
    page_size: Annotated[int, typer.Option(min=1, max=50_000)] = 5_000,
    max_rows: Annotated[int, typer.Option(min=1, help="Row cap per source per run.")] = 2_000_000,
    statement_timeout: Annotated[int, typer.Option(min=1, help="Statement timeout in seconds.")] = 120,
) -> None:
    """Read usage, subscriptions, alerts, comments and sizes from the repository (needs \\[repository]).

    Reads TABLEAU_REPO_HOST, TABLEAU_REPO_PORT (8060), TABLEAU_REPO_USER (readonly),
    TABLEAU_REPO_PASSWORD and TABLEAU_REPO_DB (workgroup). The password is never printed.
    """
    from datetime import UTC, datetime, timedelta

    from tabfree_migrate.repository import SOURCE_NAMES, RepositoryStore
    from tabfree_migrate.repository import harvest_repository as run_harvest

    try:
        connection = RepositoryConnection.from_env(os.environ)
    except ConfigError as exc:
        _fail(exc, code=2)
    store = RepositoryStore(db)
    since = datetime.now(UTC) - timedelta(days=since_days)
    try:
        with repository_reader(connection, statement_timeout * 1000) as reader:
            stats = run_harvest(reader, store, sources or SOURCE_NAMES, since, page_size, max_rows)
    except ValueError as exc:
        _fail(exc, code=2)
    except Exception as exc:  # includes psycopg errors; libpq never puts the password in messages
        _fail(exc)
    finally:
        store.close()
    for name, source in stats.sources.items():
        note = f"  ERROR {source.error}" if source.error else ""
        cap = " (row cap reached)" if source.truncated else ""
        typer.echo(f"{name:<24} {source.rows}{cap}{note}")


@app.command()
def download(
    out: Annotated[str, typer.Option(help="Directory, or s3://bucket/prefix (needs \\[aws]).")],
    db: Annotated[Path, typer.Option(help="Inventory from `crawl`.")] = Path("inventory.db"),
    kinds: Annotated[
        list[str] | None, typer.Option("--kind", help="workbooks and/or datasources (default: both).")
    ] = None,
    with_extract: Annotated[
        list[str] | None, typer.Option("--with-extract", help="Also download this LUID with its extract.")
    ] = None,
    all_extracts: Annotated[bool, typer.Option(help="Download every item with its extract too.")] = False,
    force: Annotated[bool, typer.Option(help="Download again even when unchanged.")] = False,
) -> None:
    """Download .twbx/.tdsx files (without extracts first) into content-addressed storage."""
    from tabfree_migrate import downloads
    from tabfree_migrate.blobstore import sink_for
    from tabfree_migrate.inventory import InventoryStore

    connection = _connection_or_exit()
    inventory = InventoryStore(db)
    try:
        targets = downloads.targets_from_inventory(inventory, kinds or downloads.KINDS)
    except ValueError as exc:
        _fail(exc, code=2)
    finally:
        inventory.close()
    if not targets:
        _fail(RuntimeError("nothing to download: run `tabfree-migrate crawl` first"))
    policy = downloads.all_extracts if all_extracts else downloads.extracts_for(with_extract or [])
    try:
        sink = sink_for(out)
        with content_downloader(connection) as downloader:
            stats = downloads.download_all(downloader, sink, targets, policy, force=force)
    except _FAILURES as exc:
        _fail(exc)
    for key, error in stats.errors.items():
        typer.echo(f"FAIL {key}: {error}")
    typer.echo(
        f"downloaded {stats.downloaded}, skipped {stats.skipped}, failed {len(stats.errors)}, "
        f"{stats.bytes} bytes -> {sink.describe()}"
    )


@app.command()
def capture(
    out: Annotated[str, typer.Option(help="Directory, or s3://bucket/prefix (needs \\[aws]).")],
    db: Annotated[Path, typer.Option(help="Inventory (and repository harvest) SQLite file.")] = Path(
        "inventory.db"
    ),
    formats: Annotated[
        list[str] | None, typer.Option("--format", help="png, pdf, csv, xlsx (default: all).")
    ] = None,
    permutations: Annotated[
        Path | None, typer.Option(help="JSON file of filter/parameter permutations.")
    ] = None,
    top: Annotated[int, typer.Option(min=0, help="Permutations for the N most used views.")] = 25,
    max_views: Annotated[int | None, typer.Option(min=1, help="Capture at most N views.")] = None,
    viz_width: Annotated[int, typer.Option(min=100, max=10_000)] = 1920,
    viz_height: Annotated[int, typer.Option(min=100, max=10_000)] = 1080,
    force: Annotated[bool, typer.Option(help="Capture again even when unchanged.")] = False,
) -> None:
    """Capture ground truth per view: PNG, PDF, summary CSV, crosstab and permutations."""
    from tabfree_migrate import ground_truth as gt
    from tabfree_migrate.blobstore import sink_for
    from tabfree_migrate.inventory import InventoryStore
    from tabfree_migrate.repository import RepositoryStore

    connection = _connection_or_exit()
    chosen = tuple(formats or gt.FORMATS)
    unknown = [f for f in chosen if f not in gt.FORMATS]
    if unknown:
        _fail(ValueError(f"unknown formats {', '.join(unknown)}"), code=2)
    try:
        perms = gt.load_permutations(json.loads(permutations.read_text())) if permutations else None
    except (OSError, ValueError, AttributeError) as exc:
        _fail(ValueError(f"bad permutations file: {exc}"), code=2)
    repository = RepositoryStore(db)
    usage = repository.view_usage_by_luid() or None
    repository.close()
    inventory = InventoryStore(db)
    targets = gt.targets_from_inventory(inventory, usage)[:max_views]
    inventory.close()
    if not targets:
        _fail(RuntimeError("no views to capture: run `tabfree-migrate crawl` first"))
    base = gt.ExportOptions(viz_width=viz_width, viz_height=viz_height)
    permutation_formats = [f for f in gt.PERMUTATION_FORMATS if f in chosen] or list(chosen)
    plan = gt.plan_captures(targets, base, chosen, perms, top=top, permutation_formats=permutation_formats)
    try:
        sink = sink_for(out)
        with view_exporter(connection) as exporter:
            stats = gt.capture_all(exporter, sink, plan, force=force)
    except _FAILURES as exc:
        _fail(exc)
    for key, error in stats.errors.items():
        typer.echo(f"FAIL {key}: {error}")
    typer.echo(
        f"captured {stats.captured}, skipped {stats.skipped}, failed {len(stats.errors)}, "
        f"{stats.bytes} bytes -> {sink.describe()}"
    )
