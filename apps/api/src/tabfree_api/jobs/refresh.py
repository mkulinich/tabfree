"""Data refresh (BUILD_PLAN P6.2): rebuild or top up a model's tables, then tell everyone who cares.

A refresh task (``refresh_tasks``) names a model, a mode and its tables::

    {"tables": [{"target": "orders", "database": "lake", "source": "SELECT * FROM erp_fed.dbo.orders",
                 "location": "s3://lake/warehouse/orders/", "keep": 1,
                 "incrementalKey": "updated_at", "uniqueKey": ["order_id"],
                 "columns": ["order_id", "customer", "amount", "updated_at"]}]}

- **full** on Athena: an Iceberg CTAS into a new versioned table (``orders__v20270102060000``), then
  ``CREATE OR REPLACE VIEW orders`` over it, which swaps atomically for every reader; older versions
  beyond ``keep`` are dropped. On Redshift: CTAS into ``orders__new`` and two renames in one transaction.
- **incremental**: ``MERGE`` on ``uniqueKey`` of the source rows whose ``incrementalKey`` is past the
  target's maximum (an ``INSERT`` when there is no unique key). Iceberg on Athena, or Redshift.
- **materialized_view**: ``REFRESH MATERIALIZED VIEW`` (Redshift).

Sources outside AWS are read through an Athena federated catalog or replicated first, as decided in
P5.1; the source here is a SELECT the engine can run. After success the model's data version moves to
the refresh's timestamp, so every result-cache key changes (P2.7), and the subscriptions and alerts
that run "after a refresh", cache warming and the ``model.refreshed`` webhook event follow.

Source SQL is written by administrators (refresh tasks need Refresh Extracts on the model), never by
dashboards; identifiers are checked, so a task cannot smuggle statements through a table name.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import select, update

from tabfree_api.db import schema as t
from tabfree_api.jobs.queue import Job, JobQueue
from tabfree_api.jobs.worker import Permanent, Retryable

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MODES = ("full", "incremental", "materialized_view", "rollup")


def ident(value: Any, what: str) -> str:
    text = str(value or "")
    if not _IDENT.match(text):
        raise Permanent(f"{what} must be a plain identifier (letters, digits, underscores), not {text!r}")
    return text


def _source(table: Mapping[str, Any]) -> str:
    source = str(table.get("source") or "").strip().rstrip(";")
    if not source:
        raise Permanent(f"table {table.get('target')!r} has no source")
    if ";" in source:
        raise Permanent("the source must be one SELECT statement")
    if not re.match(r"(?is)^\s*(select|with)\b", source):
        raise Permanent("the source must be a SELECT")
    return source


def full_athena(
    table: Mapping[str, Any], version: str, previous: Sequence[str]
) -> tuple[list[str], list[str]]:
    """(statements, versions to keep) for a full Iceberg refresh with a view swap."""
    db, target = ident(table.get("database"), "database"), ident(table.get("target"), "target")
    location = str(table.get("location") or "")
    if not location.startswith("s3://"):
        raise Permanent(f"table {target!r} needs an s3:// location for its Iceberg versions")
    versioned = f"{target}__v{version}"
    keep = max(0, int(table.get("keep", 1)))
    statements = [
        f"CREATE TABLE {db}.{versioned} WITH (table_type = 'ICEBERG', is_external = false, "
        f"location = '{location.rstrip('/')}/v{version}/') AS SELECT * FROM ({_source(table)}) AS source",
        f"CREATE OR REPLACE VIEW {db}.{target} AS SELECT * FROM {db}.{versioned}",
    ]
    kept = [*previous, version][-(keep + 1) :]
    statements += [f"DROP TABLE IF EXISTS {db}.{target}__v{old}" for old in previous if old not in kept]
    return statements, kept


def full_redshift(table: Mapping[str, Any], *, exists: bool) -> list[str]:
    schema = ident(table.get("schema") or table.get("database"), "schema")
    target = ident(table.get("target"), "target")
    new, old = f"{schema}.{target}__new", f"{schema}.{target}__old"
    source = _source(table)
    if not exists:
        return [f"CREATE TABLE {schema}.{target} AS SELECT * FROM ({source}) AS source"]
    return [
        f"DROP TABLE IF EXISTS {new}",
        f"DROP TABLE IF EXISTS {old}",
        f"CREATE TABLE {new} AS SELECT * FROM ({source}) AS source",
        f"ALTER TABLE {schema}.{target} RENAME TO {target}__old",
        f"ALTER TABLE {new} RENAME TO {target}",
        f"DROP TABLE {old}",
    ]


def incremental(table: Mapping[str, Any], dialect: str) -> list[str]:
    qualifier = table.get("schema") if dialect == "redshift" else table.get("database")
    target = f"{ident(qualifier, 'database')}.{ident(table.get('target'), 'target')}"
    key = ident(table.get("incrementalKey"), "incrementalKey")
    new_rows = (
        f"SELECT * FROM ({_source(table)}) AS source "
        f"WHERE source.{key} > (SELECT COALESCE(MAX({key}), {_floor(table)}) FROM {target})"
    )
    unique = [ident(k, "uniqueKey") for k in table.get("uniqueKey") or []]
    if not unique:
        return [f"INSERT INTO {target} {new_rows}"]
    columns = [ident(c, "column") for c in table.get("columns") or []]
    if not columns:
        raise Permanent("an incremental refresh with a unique key needs the column list")
    on = " AND ".join(f"target.{k} = s.{k}" for k in unique)
    sets = ", ".join(f"{c} = s.{c}" for c in columns if c not in unique)
    names = ", ".join(columns)
    values = ", ".join(f"s.{c}" for c in columns)
    merge = f"MERGE INTO {target} AS target USING ({new_rows}) AS s ON {on} "
    if sets:
        merge += f"WHEN MATCHED THEN UPDATE SET {sets} "
    return [merge + f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values})"]


def _floor(table: Mapping[str, Any]) -> str:
    """The incremental key's lowest value, for an empty target (a timestamp unless told otherwise)."""
    kind = str(table.get("keyType", "timestamp"))
    return {"integer": "-9223372036854775808", "date": "DATE '0001-01-01'"}.get(
        kind, "TIMESTAMP '0001-01-01 00:00:00'"
    )


def materialized_view(table: Mapping[str, Any]) -> list[str]:
    schema = ident(table.get("schema") or table.get("database"), "schema")
    return [f"REFRESH MATERIALIZED VIEW {schema}.{ident(table.get('target'), 'target')}"]


def rollup_tables(
    model: Mapping[str, Any], settings: Mapping[str, Any], dialect: str
) -> list[dict[str, Any]]:
    """Each declared rollup as a full-refresh table whose source is its compiled semantic query (P6.10)."""
    from tabfree_api.rollups import build_query, without_rls
    from tabfree_compiler import compile_query
    from tabfree_compiler.sql import Dialect, Session

    wanted = set(settings.get("rollups") or [])
    out = []
    for rollup in model.get("rollups", []) or []:
        if wanted and rollup["id"] not in wanted:
            continue
        compiled = compile_query(
            without_rls(model), build_query(model, rollup), dialect=cast(Dialect, dialect), session=Session()
        )
        if compiled.parameters or compiled.named_parameters:
            raise Permanent(f"rollup {rollup['id']!r} needs parameters; rollups are built from plain fields")
        database = rollup.get("database") or (model.get("connection") or {}).get("database")
        location = str(settings.get("location") or "").rstrip("/")
        out.append(
            {
                "target": rollup["table"],
                "database": database,
                "schema": (model.get("connection") or {}).get("schema") or database,
                "source": compiled.sql,
                **({"location": f"{location}/{rollup['table']}"} if location else {}),
                "keep": int(settings.get("keep", 1)),
            }
        )
    if not out:
        raise Permanent("the model declares no rollups to build")
    return out


def handle(ctx: Any, job: Job) -> Mapping[str, Any]:
    from tabfree_api.engines import EngineError, EngineTimeoutError

    site = ctx.site
    r = t.refresh_tasks.c
    with site.engine.connect() as conn:
        task = (
            conn.execute(
                select(t.refresh_tasks).where(r.site_id == site.id, r.id == str(job.payload.get("task")))
            )
            .mappings()
            .first()
        )
    if task is None:
        raise Permanent(f"no refresh task {job.payload.get('task')!r}")
    model_id = str(task["model_id"])
    model = site.models.get(model_id)
    if model is None:
        raise Permanent(f"no model {model_id!r}")
    engine = ctx.service._engine_for(model)
    if not hasattr(engine, "statements"):
        raise Permanent(f"the {getattr(engine, 'dialect', '?')} engine cannot run refresh statements")
    settings = dict(task["settings"] or {})
    mode = str(task["mode"])
    version = (ctx.clock() if hasattr(ctx, "clock") else dt.datetime.now(dt.UTC)).strftime("%Y%m%d%H%M%S")
    dialect = str(getattr(engine, "dialect", ""))
    statements: list[str] = []
    versions: dict[str, list[str]] = dict(settings.get("versions") or {})
    created = set(settings.get("created") or [])
    tables = list(settings.get("tables") or [])
    if mode == "rollup":
        tables = rollup_tables(model, settings, dialect)
        mode = "full"
    for table in tables:
        target = ident(table.get("target"), "target")
        if mode == "full" and dialect == "redshift":
            statements += full_redshift(table, exists=target in created)
            created.add(target)
        elif mode == "full":
            stmts, versions[target] = full_athena(table, version, versions.get(target, []))
            statements += stmts
        elif mode == "incremental":
            statements += incremental(table, dialect)
        elif mode == "materialized_view":
            statements += materialized_view(table)
        else:
            raise Permanent(f"unknown refresh mode {mode!r}")
    if not statements:
        raise Permanent("the refresh task has no tables")
    try:
        engine.statements(statements, timeout_s=float(settings.get("timeoutSeconds", 3600)))
    except EngineTimeoutError as exc:
        raise Retryable(str(exc)) from None
    except EngineError as exc:
        raise Retryable(str(exc)) from None
    settings.update(versions=versions, created=sorted(created), lastVersion=version)
    with site.engine.begin() as conn:
        conn.execute(
            update(t.refresh_tasks).where(r.site_id == site.id, r.id == task["id"]).values(settings=settings)
        )
    site.bump_data_version(model_id, version)
    after_refresh_events(ctx, model_id, version)
    return {"model": model_id, "mode": mode, "statements": len(statements), "dataVersion": version}


def after_refresh_events(ctx: Any, model_id: str, version: str) -> None:
    from tabfree_api.jobs import webhooks
    from tabfree_api.jobs.scheduler import after_refresh
    from tabfree_api.jobs.warm import enqueue_warm

    queue = JobQueue(ctx.site.engine)
    after_refresh(ctx.site.engine, queue, ctx.site.id, model_id)
    enqueue_warm(ctx, queue, model_id)
    webhooks.emit(ctx, "model.refreshed", {"model": model_id, "dataVersion": version})
