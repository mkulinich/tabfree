"""Generated Superstore-shaped sample data for local development, tests and the preview."""

from __future__ import annotations

import datetime as dt
import os
import random
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
