"""Usage in the lake (BUILD_PLAN P6.7): the audit log exported daily, and TabFree dashboards over it.

The ``usage_export`` job writes one gzipped JSON Lines file per site and day to the usage lake
(``TABFREE_USAGE_LAKE``: ``s3://bucket/prefix`` or a local folder), under
``events/site=<site>/day=<YYYY-MM-DD>/events.jsonl.gz``. Athena reads them through the table in
``USAGE_DDL`` (a JSON SerDe, partitioned by site and day); the built-in model ``tabfree_usage`` and the
dashboard ``site-usage`` (``python -m tabfree_api.sites install-usage``) sit in an *Administration*
project with no rules, so only site administrators see them: the events name people.

Events carry no data values: who, what, when, outcome, durations, cache use, bytes scanned.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select

from tabfree_api.db import schema as t

ACTIONS = ("view", "query_metrics", "export", "sign_in", "subscription_send", "alert_notify", "job_failed")
DATABASE = "tabfree_usage"
USAGE_DDL = f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.events (
  at string, actor string, action string, resource_kind string, resource_id string, outcome string,
  duration_ms bigint, cache string, scanned_bytes bigint, row_count bigint, query string
)
PARTITIONED BY (site string, day date)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION '<usage lake>/events/'
TBLPROPERTIES ('projection.enabled'='true', 'projection.site.type'='injected',
  'projection.day.type'='date', 'projection.day.format'='yyyy-MM-dd', 'projection.day.range'='2024-01-01,NOW',
  'storage.location.template'='<usage lake>/events/site=${{site}}/day=${{day}}/')"""

Writer = Callable[[str, bytes], None]
TYPES = {
    "at": "VARCHAR", "day": "DATE", "actor": "VARCHAR", "action": "VARCHAR", "resource_kind": "VARCHAR",
    "resource_id": "VARCHAR", "outcome": "VARCHAR", "duration_ms": "BIGINT", "cache": "VARCHAR",
    "scanned_bytes": "BIGINT", "row_count": "BIGINT", "query": "VARCHAR",
}  # fmt: skip
COLUMNS = "{" + ", ".join(f"'{k}': '{v}'" for k, v in TYPES.items()) + "}"


def events(engine: Any, site_id: str, day: dt.date) -> Iterator[dict[str, Any]]:
    a = t.audit_events.c
    start = dt.datetime.combine(day, dt.time(), dt.UTC)
    with engine.connect() as conn:
        rows = conn.execute(
            select(a.at, a.actor, a.action, a.resource_kind, a.resource_id, a.outcome, a.detail)
            .where(
                a.site_id == site_id,
                a.at >= start,
                a.at < start + dt.timedelta(days=1),
                a.action.in_(ACTIONS),
            )
            .order_by(a.id)
        )
        for at, actor, action, kind, resource, outcome, detail in rows:
            detail = detail or {}
            when = at if at.tzinfo else at.replace(tzinfo=dt.UTC)
            yield {
                "at": when.isoformat(),
                "day": day.isoformat(),
                "actor": actor,
                "action": action,
                "resource_kind": kind,
                "resource_id": resource,
                "outcome": outcome,
                "duration_ms": int(detail.get("duration_ms", 0) or 0),
                "cache": detail.get("cache"),
                "scanned_bytes": int(detail.get("scanned", 0) or 0),
                "row_count": int(detail.get("rows", 0) or 0),
                "query": detail.get("query"),
            }


def key(site_id: str, day: dt.date) -> str:
    return f"events/site={site_id}/day={day.isoformat()}/events.jsonl.gz"


def export_day(engine: Any, site_id: str, day: dt.date, write: Writer) -> int:
    lines = [json.dumps(e, separators=(",", ":")) for e in events(engine, site_id, day)]
    write(key(site_id, day), gzip.compress(("\n".join(lines) + ("\n" if lines else "")).encode(), mtime=0))
    return len(lines)


def writer_for(location: str) -> Writer:
    if location.startswith("s3://"):
        import boto3  # the ``tabfree-api[aws]`` extra

        bucket, _, prefix = location.removeprefix("s3://").partition("/")
        client = boto3.client("s3")

        def put(name: str, data: bytes) -> None:
            client.put_object(Bucket=bucket, Key=f"{prefix.rstrip('/')}/{name}".lstrip("/"), Body=data)

        return put
    root = Path(location)

    def write(name: str, data: bytes) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    return write


def handle(ctx: Any, job: Any) -> Mapping[str, Any]:
    """The ``usage_export`` job: yesterday (or ``payload.day``) for this site."""
    from tabfree_api.jobs.worker import Permanent

    location = ctx.settings.usage_lake
    if not location:
        raise Permanent("set TABFREE_USAGE_LAKE (s3://bucket/prefix or a folder) to export usage")
    day = (
        dt.date.fromisoformat(str(job.payload.get("day")))
        if job.payload.get("day")
        else (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1))
    )
    count = export_day(ctx.site.engine, ctx.site.id, day, writer_for(str(location)))
    loader = getattr(ctx, "usage_loader", None)
    if loader is not None:  # development: make the file queryable in the sample engine
        loader(Path(str(location)))
    return {"day": day.isoformat(), "events": count}


def load_into_duckdb(connection: Any, root: Path) -> None:
    """Development and tests: the exported files as ``tabfree_usage.events`` in DuckDB."""
    files = sorted(root.glob("events/site=*/day=*/events.jsonl.gz"))
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {DATABASE}")
    if not files:
        return
    paths = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
    connection.execute(
        f"CREATE OR REPLACE TABLE {DATABASE}.events AS SELECT * FROM read_json([{paths}], "  # noqa: S608
        f"format='newline_delimited', columns={COLUMNS})"
    )
