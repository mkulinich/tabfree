"""``tabfree-migrate`` command line. Every command is read-only against Tableau."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, cast

import typer

from tabfree_migrate import __version__
from tabfree_migrate.corpus import verify_corpus
from tabfree_migrate.smoke import format_report, run_smoke
from tabfree_migrate.tableau import (
    ConfigError,
    TableauAccessError,
    TableauConnection,
    TableauReader,
    TscReader,
)

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
    """Export every table of a .hyper extract to Parquet with parity statistics (needs [hyper])."""
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
        usage=rpt.load_usage(usage) if usage else None,
        inventory=rpt.inventory_details(inventory) if inventory else None,
    )
    md, js = rpt.write_report(out, workbooks, connections, estate)
    typer.echo(f"{len(workbooks)} workbooks -> {md} and {js}")
