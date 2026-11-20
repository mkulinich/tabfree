"""``tabfree-migrate`` command line. Every command is read-only against Tableau."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

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
    root: Annotated[
        Path, typer.Option(help="Repository root that contains corpus/manifest.json.")
    ] = Path("."),
) -> None:
    """Verify corpus files against corpus/manifest.json: presence, hashes and XML structure."""
    report = verify_corpus(root)
    for problem in report.problems:
        typer.echo(f"FAIL {problem}")
    typer.echo(
        f"{report.checked} checked, {report.skipped} external, {len(report.problems)} problems"
    )
    if report.problems:
        raise typer.Exit(code=1)
