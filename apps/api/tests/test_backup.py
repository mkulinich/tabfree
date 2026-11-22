"""P7.1: a logical backup restores every table, row for row, and refuses damaged or mismatched input."""

from __future__ import annotations

import gzip
import io
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, insert, select, text

from tabfree_api.backup import BackupError, export, restore, tables, verify
from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db import schema as t
from tabfree_api.db.seed import import_site
from tabfree_api.db.store import now
from tabfree_api.settings import Settings

POSTGRES = os.environ.get("TABFREE_TEST_POSTGRES_URL")


def seeded(url: str) -> Any:
    engine = connect(url)
    upgrade(engine)
    db = import_site(engine, Site(Settings().site_file))
    db.set_favorite("erin", "dashboard", "sales-overview", True)
    with engine.begin() as conn:
        conn.execute(
            insert(t.migration_items).values(
                site_id="dev", id="m1", title="T", workbook="W", status="manual", report={"x": [1, "é"]},
                reference_png=b"\x89PNG\x00\xff", reference_csv={}, repair_iterations=0, note="",
                usage=0, created_at=now(), updated_at=now(),
            )
        )  # fmt: skip
    return engine


def counts(engine: Any) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            tb.name: int(conn.execute(select(func.count()).select_from(tb)).scalar() or 0) for tb in tables()
        }


def fresh(url_or_path: str) -> Any:
    engine = connect(url_or_path)
    upgrade(engine)
    return engine


def test_round_trip_restores_every_row(tmp_path: Path) -> None:
    source = seeded(f"sqlite:///{tmp_path / 'a.db'}")
    path = tmp_path / "backup.jsonl.gz"
    with path.open("wb") as out:
        exported = export(source, out)
    assert exported["content_revisions"] >= 3
    assert exported["migration_items"] == 1
    with path.open("rb") as f:
        summary = verify(f)
    assert summary["counts"] == exported
    target = fresh(f"sqlite:///{tmp_path / 'b.db'}")
    restore(target, path)
    assert counts(target) == counts(source)
    with target.connect() as conn:
        row = conn.execute(select(t.migration_items.c.reference_png, t.migration_items.c.report)).first()
    assert row is not None
    assert bytes(row[0]) == b"\x89PNG\x00\xff"
    assert row[1] == {"x": [1, "é"]}
    # Restoring twice is refused: the database is no longer empty.
    with pytest.raises(BackupError, match="not empty"):
        restore(target, path)


def test_damaged_or_mismatched_backups_are_refused(tmp_path: Path) -> None:
    source = seeded(f"sqlite:///{tmp_path / 'a.db'}")
    buffer = io.BytesIO()
    export(source, buffer)
    lines = gzip.decompress(buffer.getvalue()).decode().splitlines()
    tampered = [lines[0], *[line.replace("erin", "eve") for line in lines[1:-1]], lines[-1]]
    with pytest.raises(BackupError, match="damaged"):
        verify(io.BytesIO(gzip.compress("\n".join(tampered).encode())))
    with pytest.raises(BackupError, match="truncated"):
        verify(io.BytesIO(gzip.compress("\n".join(lines[:-1]).encode())))
    path = tmp_path / "b.jsonl.gz"
    path.write_bytes(buffer.getvalue())
    old = connect(f"sqlite:///{tmp_path / 'old.db'}")
    upgrade(old, "0005")
    with pytest.raises(BackupError, match="migration"):
        restore(old, path)


@pytest.mark.skipif(not POSTGRES, reason="set TABFREE_TEST_POSTGRES_URL to run against Postgres")
def test_sqlite_backup_restores_into_postgres(tmp_path: Path) -> None:
    source = seeded(f"sqlite:///{tmp_path / 'a.db'}")
    path = tmp_path / "backup.jsonl.gz"
    with path.open("wb") as out:
        export(source, out)
    assert POSTGRES is not None
    target = connect(POSTGRES)
    with target.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    upgrade(target)
    restore(target, path)
    assert counts(target) == counts(source)
    # New rows get new ids after the restored ones (sequences moved past them).
    with target.begin() as conn:
        conn.execute(insert(t.audit_events).values(at=now(), action="x", outcome="ok", detail={}))
