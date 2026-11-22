"""Logical backup and restore of the metadata database (BUILD_PLAN P7.1).

    python -m tabfree_api.backup export backup.jsonl.gz      # every table, in dependency order
    python -m tabfree_api.backup verify backup.jsonl.gz      # read it back: tables, rows, checksum
    python -m tabfree_api.backup restore backup.jsonl.gz     # into an empty, migrated database

On AWS the primary protection is Aurora's continuous backup (point-in-time recovery) and AWS Backup
snapshots copied to a second region (docs/operations/disaster-recovery.md). This logical format is
for what those cannot do: moving between Postgres and another database or account, self-hosted
installs, and restore drills that check the data itself (tests restore every table and compare).

The file is gzipped JSON Lines: a header (format, migration revision, table list, time), then one line
per row ``{"t": table, "r": row}``, then a trailer with row counts and a SHA-256 over the row lines.
Restoring refuses a file from another migration revision (migrate first), a database that already
has data, or a file whose trailer does not match.

The file contains everything in the database, including password hashes, OAuth token hashes and
webhook secrets: treat it like the database (encrypted storage, restricted access).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import decimal
import gzip
import hashlib
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import IO, Any

from sqlalchemy import Engine, LargeBinary, func, insert, select, text

from tabfree_api.db import schema as t

FORMAT = "tabfree/backup@1"
BATCH = 1000


class BackupError(ValueError):
    pass


def revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        try:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            return None


def _encode(value: Any) -> Any:
    if isinstance(value, bytes | memoryview):
        return {"$b": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, dt.datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        return {"$dt": aware.isoformat()}
    if isinstance(value, dt.date):
        return {"$d": value.isoformat()}
    if isinstance(value, decimal.Decimal):
        return {"$n": str(value)}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        ((key, inner),) = value.items()
        if key == "$b":
            return base64.b64decode(inner)
        if key == "$dt":
            return dt.datetime.fromisoformat(inner)
        if key == "$d":
            return dt.date.fromisoformat(inner)
        if key == "$n":
            return decimal.Decimal(inner)
    return value


def tables() -> list[Any]:
    """Every table, parents before children (foreign keys)."""
    return list(t.metadata.sorted_tables)


def export(engine: Engine, out: IO[bytes]) -> dict[str, int]:
    rev = revision(engine)
    names = [table.name for table in tables()]
    counts: dict[str, int] = {}
    digest = hashlib.sha256()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        header = {
            "format": FORMAT,
            "revision": rev,
            "tables": names,
            "at": dt.datetime.now(dt.UTC).isoformat(),
        }
        gz.write((json.dumps(header) + "\n").encode())
        with engine.connect() as conn:
            for table in tables():
                order = list(table.primary_key.columns) or list(table.columns)[:1]
                n = 0
                for row in (
                    conn.execution_options(stream_results=True)
                    .execute(select(table).order_by(*order))
                    .mappings()
                ):
                    line = json.dumps(
                        {"t": table.name, "r": {k: _encode(v) for k, v in row.items()}}, sort_keys=True
                    )
                    digest.update(line.encode())
                    gz.write((line + "\n").encode())
                    n += 1
                counts[table.name] = n
        gz.write((json.dumps({"end": True, "counts": counts, "sha256": digest.hexdigest()}) + "\n").encode())
    return counts


def _lines(source: IO[bytes]) -> Iterator[dict[str, Any]]:
    with gzip.GzipFile(fileobj=source, mode="rb") as gz:
        for raw in gz:
            if raw.strip():
                yield json.loads(raw)


def verify(source: IO[bytes]) -> dict[str, Any]:
    """Read a backup through: header, every row, and the trailer's counts and checksum."""
    lines = _lines(source)
    header = next(lines, None)
    if not header or header.get("format") != FORMAT:
        raise BackupError("not a TabFree backup")
    counts: dict[str, int] = {}
    digest = hashlib.sha256()
    trailer = None
    for line in lines:
        if line.get("end"):
            trailer = line
            break
        digest.update(json.dumps(line, sort_keys=True).encode())
        counts[line["t"]] = counts.get(line["t"], 0) + 1
    if trailer is None:
        raise BackupError("the backup is truncated (no trailer)")
    expected = {k: v for k, v in trailer["counts"].items() if v}
    if counts != expected or digest.hexdigest() != trailer["sha256"]:
        raise BackupError("the backup does not match its trailer (damaged)")
    return {"revision": header["revision"], "at": header["at"], "counts": trailer["counts"]}


def restore(engine: Engine, source_path: Path) -> dict[str, int]:
    """Load a verified backup into an empty database migrated to the same revision."""
    with source_path.open("rb") as f:
        summary = verify(f)
    if summary["revision"] != revision(engine):
        raise BackupError(
            f"the backup is from migration {summary['revision']}, the database is at {revision(engine)}; "
            "upgrade the database (python -m tabfree_api.db upgrade) to the backup's revision first"
        )
    by_name = {table.name: table for table in tables()}
    with engine.connect() as conn:
        for table in tables():
            if conn.execute(select(func.count()).select_from(table)).scalar():
                raise BackupError(
                    f"the database is not empty ({table.name} has rows); restore into a new one"
                )
    counts: dict[str, int] = {}
    with engine.begin() as conn, source_path.open("rb") as f:
        lines = _lines(f)
        next(lines)  # header
        batch: list[dict[str, Any]] = []
        current: str | None = None

        def flush() -> None:
            if batch and current is not None:
                conn.execute(insert(by_name[current]), batch)
                counts[current] = counts.get(current, 0) + len(batch)
                batch.clear()

        for line in lines:
            if line.get("end"):
                break
            if line["t"] != current or len(batch) >= BATCH:
                flush()
                current = line["t"]
            table = by_name[current]
            row = {}
            for key, value in line["r"].items():
                decoded = _decode(value)
                column = table.columns.get(key)
                if column is not None and isinstance(column.type, LargeBinary) and isinstance(decoded, str):
                    decoded = decoded.encode()
                row[key] = decoded
            batch.append(row)
        flush()
        if engine.dialect.name == "postgresql":
            # Identity columns continue after the restored ids.
            for table in tables():
                for column in table.primary_key.columns:
                    if column.autoincrement is True or (
                        column.autoincrement == "auto" and column.type.python_type is int
                    ):
                        conn.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "  # noqa: S608
                                f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 1))"
                            )
                        )
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    from tabfree_api.db import connect
    from tabfree_api.settings import Settings

    parser = argparse.ArgumentParser(prog="python -m tabfree_api.backup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument("command", choices=["export", "verify", "restore"])
    parser.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            with args.file.open("rb") as f:
                print(json.dumps(verify(f), indent=2))
            return 0
        settings = Settings()
        if settings.database_url is None:
            print("set TABFREE_DATABASE_URL (or TABFREE_DB_HOST, _USER, _PASSWORD)", file=sys.stderr)
            return 2
        engine = connect(settings.database_url.get_secret_value())
        if args.command == "export":
            with args.file.open("xb") as out:
                counts = export(engine, out)
            print(f"exported {sum(counts.values())} rows from {len(counts)} tables to {args.file}")
        else:
            counts = restore(engine, args.file)
            print(f"restored {sum(counts.values())} rows into {len(counts)} tables")
    except (BackupError, FileExistsError) as exc:
        print(f"backup: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
