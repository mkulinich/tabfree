"""Generated Superstore-shaped sample data for local development, tests and the preview.

The same tables can be copied into the local stack's Trino, so the example dashboards run there with
``TABFREE_ENGINE=trino`` (the stack's ``sample-data`` service does this on ``make dev-up``):

    uv run python -m tabfree_api.sample trino --url http://localhost:8080
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def superstore_dir() -> Path:
    """The folder with the Superstore parquet files: TABFREE_SUPERSTORE_DIR, else the repository's corpus.

    An installed package (the container image) is not inside a checkout, so the demo sets the variable.
    """
    override = os.environ.get("TABFREE_SUPERSTORE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "corpus" / "derived" / "superstore"


#: Tableau's Sample - Superstore, converted from the corpus (corpus/derived/superstore/PROVENANCE.md).
SUPERSTORE = superstore_dir()

#: Regional managers, as in the example model's people table (the RLS rule keys on them).
MANAGERS = {"East": "alice", "West": "bob", "Central": "carol", "South": "dave"}


_STATES = {
    "East": ["New York", "Pennsylvania", "Massachusetts", "Ohio"],
    "West": ["California", "Washington", "Oregon", "Colorado"],
    "Central": ["Texas", "Illinois", "Michigan", "Minnesota"],
    "South": ["Florida", "Georgia", "Virginia", "Tennessee"],
}
_PRODUCTS = {
    "Furniture": ["Chairs", "Tables", "Bookcases", "Furnishings"],
    "Office Supplies": ["Paper", "Binders", "Storage", "Art"],
    "Technology": ["Phones", "Accessories", "Machines", "Copiers"],
}
_FIRST = ["Ana", "Ben", "Cleo", "Dev", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jun", "Kai", "Lena"]
_LAST = ["Park", "Ruiz", "Stone", "Okafor", "Nguyen", "Weber"]


def sample_rows(seed: int = 7, count: int = 2400) -> list[tuple[Any, ...]]:
    """Deterministic Superstore-shaped orders for 2023 and 2024."""
    rng = random.Random(seed)  # noqa: S311 - sample data, not security
    customers = [
        (f"{first} {last}", rng.choice(["Consumer", "Corporate", "Home Office"]))
        for first in _FIRST
        for last in _LAST
    ]
    start = dt.date(2023, 1, 1)
    rows = []
    for i in range(count):
        name, segment = rng.choice(customers)
        region = rng.choices(list(_STATES), weights=[3, 3, 2, 2])[0]
        category = rng.choices(list(_PRODUCTS), weights=[2, 5, 3])[0]
        sales = round(rng.lognormvariate(4.5 if category != "Technology" else 5.2, 0.9), 2)
        discount = rng.choice([0, 0, 0, 0.1, 0.2, 0.3])
        margin = rng.uniform(-0.05, 0.4) - discount / 2
        rows.append(
            (
                i + 1,
                f"ORD-{10000 + i // 2}",
                start + dt.timedelta(days=rng.randrange(730)),
                rng.choice(["Standard", "Second Class", "First Class", "Same Day"]),
                name,
                segment,
                region,
                rng.choice(_STATES[region]),
                category,
                rng.choice(_PRODUCTS[category]),
                sales,
                rng.randint(1, 9),
                discount,
                round(sales * margin, 2),
            )
        )
    return rows


def load_sample_data(con: Any) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS tabfree_lake")
    con.execute(
        "CREATE OR REPLACE TABLE tabfree_lake.orders (row_id INTEGER, order_id VARCHAR, order_date DATE, "
        "ship_mode VARCHAR, customer_name VARCHAR, segment VARCHAR, region VARCHAR, state VARCHAR, "
        "category VARCHAR, sub_category VARCHAR, sales DOUBLE, quantity INTEGER, discount DOUBLE, "
        "profit DOUBLE)"
    )
    con.executemany(
        "INSERT INTO tabfree_lake.orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sample_rows()
    )
    con.execute("CREATE OR REPLACE TABLE tabfree_lake.people (region VARCHAR, regional_manager VARCHAR)")
    con.executemany("INSERT INTO tabfree_lake.people VALUES (?, ?)", list(MANAGERS.items()))


def load_superstore(con: Any, folder: Path = SUPERSTORE) -> bool:
    """Load the real Superstore tables as tabfree_lake.superstore_*; False if the files are absent."""
    if not (folder / "orders.parquet").is_file():
        return False
    con.execute("CREATE SCHEMA IF NOT EXISTS tabfree_lake")
    for table in ("orders", "people", "returns"):  # constant names, never input
        path = folder / f"{table}.parquet"
        sql = f"CREATE OR REPLACE TABLE tabfree_lake.superstore_{table} AS SELECT * FROM read_parquet(?)"  # noqa: S608
        con.execute(sql, [str(path)])
    return True


# ---- the same tables in Trino (the local stack) -------------------------------------------------

#: DuckDB column types of the sample tables and their Trino equivalents.
_TRINO_TYPES = {
    "VARCHAR": "varchar",
    "DOUBLE": "double",
    "FLOAT": "double",
    "BIGINT": "bigint",
    "INTEGER": "integer",
    "SMALLINT": "integer",
    "TINYINT": "integer",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp(6)",
}


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_into_trino(
    engine: Any,
    *,
    schema: str = "tabfree_lake",
    location: str | None = None,
    folder: Path | None = None,
    batch: int = 400,
) -> dict[str, str]:
    """Copy the sample tables into ``schema`` of the engine's Trino catalog; ``{table: what happened}``.

    ``engine`` is a :class:`tabfree_api.engines.TrinoEngine` (anything with ``statements``). A table
    that already holds the right number of rows is left alone, so this is safe to run on every start.
    The Superstore tables are skipped when their parquet files are absent.
    """
    from tabfree_api.engines import DuckDBEngine, trino_cast_literal

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", schema):
        raise ValueError(f"invalid schema name {schema!r}")
    if location is not None and not re.fullmatch(r"s3://[a-z0-9.\-]{3,63}(/[A-Za-z0-9_.\-/]*)?", location):
        raise ValueError(f"invalid S3 location {location!r}")
    con = DuckDBEngine.in_memory().connection
    load_sample_data(con)
    load_superstore(con, folder if folder is not None else superstore_dir())

    # Names below are the validated schema, table names created above and column names read back from
    # DuckDB (quoted); values are sample data sent as typed literals.
    where = f" WITH (location = '{location}')" if location else ""
    engine.statements([f"CREATE SCHEMA IF NOT EXISTS {schema}{where}"])
    present = {
        row[0]
        for row in engine.statements(
            [f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'"]  # noqa: S608
        )[0]
    }
    tables = [
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'tabfree_lake' ORDER BY 1"
        ).fetchall()
    ]
    done: dict[str, str] = {}
    for table in tables:
        source = f"tabfree_lake.{_quoted(table)}"
        target = f"{schema}.{_quoted(table)}"
        rows = con.execute(f"SELECT * FROM {source}").fetchall()  # noqa: S608
        if table in present:
            count = engine.statements([f"SELECT count(*) FROM {target}"])[0][0][0]  # noqa: S608
            if int(count) == len(rows):
                done[table] = f"already loaded ({len(rows)} rows)"
                continue
        described = con.execute(f"DESCRIBE {source}").fetchall()
        types = [_TRINO_TYPES[str(column[1]).upper()] for column in described]
        columns = ", ".join(f"{_quoted(column[0])} {t}" for column, t in zip(described, types, strict=True))
        engine.statements([f"DROP TABLE IF EXISTS {target}", f"CREATE TABLE {target} ({columns})"])
        for start in range(0, len(rows), batch):
            values = ", ".join(
                "(" + ", ".join(trino_cast_literal(v, t) for v, t in zip(row, types, strict=True)) + ")"
                for row in rows[start : start + batch]
            )
            engine.statements([f"INSERT INTO {target} VALUES {values}"], timeout_s=300)  # noqa: S608
        done[table] = f"loaded {len(rows)} rows"
    return done


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tabfree_api.sample", description=__doc__.split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    trino = commands.add_parser("trino", help="copy the sample tables into the local stack's Trino")
    trino.add_argument("--url", default="http://localhost:8080")
    trino.add_argument("--catalog", default="iceberg")
    trino.add_argument("--schema", default="tabfree_lake")
    trino.add_argument("--location", default=None, help="S3 location for a new schema")
    args = parser.parse_args(argv)

    from tabfree_api.engines import TrinoEngine

    engine = TrinoEngine(url=args.url, catalog=args.catalog, schema=args.schema, user="tabfree-sample")
    for table, outcome in load_into_trino(engine, schema=args.schema, location=args.location).items():
        print(f"{args.catalog}.{args.schema}.{table}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
