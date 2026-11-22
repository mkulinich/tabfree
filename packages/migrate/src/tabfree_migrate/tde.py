"""Legacy ``.tde`` extracts: flag for upgrade, or upgrade with the pinned legacy Hyper API (P2.8).

Current Hyper API releases cannot open ``.tde`` files. Two documented ways out:

1. **Upgrade on Tableau** (preferred): refresh or upgrade the extract on Tableau Server, or open
   it in Tableau Desktop 2024.2 or earlier (the last release that reads ``.tde``), then download
   it again. Tableau Server has refused ``.tde`` uploads since 2023.1.
2. **Legacy reader**: Tableau's ``tde-to-hyper`` sample reads ``.tde`` through an undocumented
   ``external(<path>, format => 'tde', "table" => ...)`` table function that only
   ``tableauhyperapi==0.0.17537`` provides. :func:`upgrade_tde` does the same, writing a ``.hyper``
   that the normal converter then exports. It runs only in the legacy image
   (``infra/docker/converter/Dockerfile.tde-legacy``) and refuses any other Hyper API version.

:func:`upgrade_advice` is what ``convert-batch`` records for a ``.tde`` when it does not upgrade:
the extract is reported as ``needs-upgrade``, never skipped silently.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

LEGACY_HYPER_API_VERSION = "0.0.17537"
LEGACY_IMAGE = "infra/docker/converter/Dockerfile.tde-legacy"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def list_tables_sql(path: str) -> str:
    """Tables in a .tde, from its SYS catalog (same query as Tableau's tde-to-hyper sample)."""
    src = _quote_literal(path)
    return (
        'SELECT "SCHEMAS_NAME", "TABLES_NAME" '
        f"FROM external({src}, format => 'tde', \"table\" => 'SYS.TABLES') "
        f"JOIN external({src}, format => 'tde', \"table\" => 'SYS.SCHEMAS') "
        'ON "TABLES_PARENT" = "SCHEMAS_ID" '
        "WHERE \"SCHEMAS_NAME\" <> 'SYS' AND \"TABLES_NAME\" <> '$TableauMetadata' "
        'AND "TABLES_ACTIVE" AND "SCHEMAS_ACTIVE"'
    )


def copy_table_sql(path: str, schema: str, table: str) -> str:
    """``CREATE TABLE`` in the target .hyper from one .tde table."""
    return (
        f"CREATE TABLE {_quote_ident(schema)}.{_quote_ident(table)} AS "
        f"SELECT * FROM external({_quote_literal(path)}, format => 'tde', "
        f'"table" => {_quote_literal(f"{schema}.{table}")})'
    )


def installed_hyper_api_version() -> str | None:
    try:
        return importlib.metadata.version("tableauhyperapi")
    except importlib.metadata.PackageNotFoundError:
        return None


def tde_reader_available() -> bool:
    """True only with the pinned legacy Hyper API, the one release known to read .tde."""
    return installed_hyper_api_version() == LEGACY_HYPER_API_VERSION


def upgrade_advice() -> dict[str, Any]:
    return {
        "status": "needs-upgrade",
        "reason": "legacy .tde extract: the current Hyper API cannot read it",
        "options": [
            "Refresh or upgrade the extract on Tableau Server (or in Tableau Desktop 2024.2 or "
            "earlier), download it again, and re-run convert-batch.",
            f"Run `tabfree-migrate convert-batch --tde upgrade` in the legacy image ({LEGACY_IMAGE}, "
            f"tableauhyperapi=={LEGACY_HYPER_API_VERSION}); it writes .hyper files under "
            "upgraded/ in the output, which the normal image then converts.",
        ],
    }


def upgrade_tde(src: Path, dst: Path, log_dir: Path | None = None) -> list[str]:
    """Copy every table of ``src`` (.tde) into a new ``dst`` (.hyper). Legacy image only."""
    version = installed_hyper_api_version()
    if version != LEGACY_HYPER_API_VERSION:
        raise RuntimeError(
            f"reading .tde needs tableauhyperapi=={LEGACY_HYPER_API_VERSION} (found {version}); "
            f"use {LEGACY_IMAGE}"
        )
    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry

    parameters = {"log_dir": str(log_dir or dst.parent)}
    copied: list[str] = []
    with (
        HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters=parameters) as hyper,
        Connection(hyper.endpoint, str(dst), CreateMode.CREATE_AND_REPLACE) as conn,
    ):
        tables = conn.execute_list_query(list_tables_sql(str(src)))
        for schema, table in tables:
            conn.execute_command(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(str(schema))}")
            conn.execute_command(copy_table_sql(str(src), str(schema), str(table)))
            copied.append(f"{schema}.{table}")
    return copied
