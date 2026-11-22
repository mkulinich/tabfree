"""Copying the sample tables into Trino, against a fake engine (the real run is on the local stack)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tabfree_api.sample import SUPERSTORE, load_into_trino

pytest.importorskip("duckdb")


class FakeTrino:
    """Records statements; answers the catalog and count queries from ``existing``."""

    def __init__(self, existing: dict[str, int] | None = None) -> None:
        self.existing = existing or {}
        self.sent: list[str] = []

    def statements(self, sqls: list[str], *, timeout_s: float | None = None) -> list[list[list[Any]]]:
        out: list[list[list[Any]]] = []
        for sql in sqls:
            self.sent.append(sql)
            if "information_schema.tables" in sql:
                out.append([[name] for name in self.existing])
            elif sql.startswith("SELECT count(*)"):
                table = sql.rsplit('"', 2)[1]
                out.append([[self.existing[table]]])
            else:
                out.append([])
        return out


def test_creates_and_fills_the_generated_tables(tmp_path: Path) -> None:
    fake = FakeTrino()
    done = load_into_trino(
        fake, schema="lake", location="s3://tabfree-lake/warehouse/lake", folder=tmp_path, batch=1000
    )
    assert done == {"orders": "loaded 2400 rows", "people": "loaded 4 rows"}  # no Superstore files here
    assert (
        fake.sent[0]
        == "CREATE SCHEMA IF NOT EXISTS lake WITH (location = 's3://tabfree-lake/warehouse/lake')"
    )
    creates = [s for s in fake.sent if s.startswith("CREATE TABLE")]
    assert creates[0].startswith(
        'CREATE TABLE lake."orders" ("row_id" integer, "order_id" varchar, "order_date" date'
    )
    inserts = [s for s in fake.sent if s.startswith('INSERT INTO lake."orders"')]
    assert len(inserts) == 3  # 2400 rows in batches of 1000
    assert inserts[0].startswith(
        "INSERT INTO lake.\"orders\" VALUES (CAST(1 AS integer), CAST('ORD-10000' AS varchar), CAST('20"
    )


def test_leaves_a_fully_loaded_table_alone(tmp_path: Path) -> None:
    fake = FakeTrino({"orders": 2400, "people": 3})
    done = load_into_trino(fake, folder=tmp_path)
    assert done == {"orders": "already loaded (2400 rows)", "people": "loaded 4 rows"}  # people was short
    assert not any("orders" in s and s.startswith(("DROP", "CREATE TABLE", "INSERT")) for s in fake.sent)
    assert 'DROP TABLE IF EXISTS tabfree_lake."people"' in fake.sent


@pytest.mark.skipif(not (SUPERSTORE / "orders.parquet").is_file(), reason="Superstore data absent")
def test_includes_superstore_when_the_files_are_there() -> None:
    fake = FakeTrino()
    done = load_into_trino(fake, folder=SUPERSTORE)
    assert done["superstore_orders"] == "loaded 9994 rows"
    assert set(done) == {"orders", "people", "superstore_orders", "superstore_people", "superstore_returns"}


def test_names_are_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid schema name"):
        load_into_trino(FakeTrino(), schema="lake; DROP", folder=tmp_path)
    with pytest.raises(ValueError, match="invalid S3 location"):
        load_into_trino(FakeTrino(), location="s3://bucket/x') --", folder=tmp_path)
