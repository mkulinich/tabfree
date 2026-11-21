"""P3.4: row-level security with impersonated personas — filter policies, entitlement tables
(by username, email or group), exemptions, and RLS applied before FIXED LODs and totals."""

from __future__ import annotations

from typing import Any

import pytest

from tabfree_compiler import CompileError, Session, compile_query

duckdb = pytest.importorskip("duckdb")

ORDERS = [
    # region, segment, sales
    ("East", "Consumer", 100.0),
    ("East", "Corporate", 50.0),
    ("West", "Consumer", 200.0),
    ("West", "Corporate", 25.0),
    ("Central", "Consumer", 10.0),
]
ENTITLEMENTS = [
    # who, region
    ("alice", "East"),
    ("carol@example.com", "Central"),
    ("West Team", "West"),
    ("bob", "East"),
    ("bob", "West"),
]


def model(*policies: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "tabfree/model@0",
        "id": "sales",
        "connection": {"engine": "athena", "database": "main"},
        "tables": [
            {
                "id": "orders",
                "source": {"table": "orders"},
                "columns": [
                    {"name": "region", "type": "string"},
                    {"name": "segment", "type": "string"},
                    {"name": "sales", "type": "float"},
                ],
            },
            {
                "id": "entitlements",
                "source": {"table": "entitlements"},
                "columns": [{"name": "who", "type": "string"}, {"name": "region", "type": "string"}],
            },
        ],
        "fields": [
            {
                "id": "region",
                "name": "Region",
                "role": "dimension",
                "type": "string",
                "table": "orders",
                "column": "region",
            },
            {
                "id": "segment",
                "name": "Segment",
                "role": "dimension",
                "type": "string",
                "table": "orders",
                "column": "segment",
            },
            {
                "id": "sales",
                "name": "Sales",
                "role": "measure",
                "type": "float",
                "table": "orders",
                "column": "sales",
                "defaultAggregation": "sum",
            },
            {
                "id": "region_total",
                "name": "Region Total",
                "role": "measure",
                "type": "float",
                "formula": "{FIXED [Region]: SUM([Sales])}",
            },
        ],
        "rls": list(policies),
    }


def entitlement(match_on: str = "username", **extra: Any) -> dict[str, Any]:
    return {
        "id": f"by_{match_on}",
        "entitlement": {
            "table": "entitlements",
            "userColumn": "who",
            "matchOn": match_on,
            "keys": [{"field": "Region", "column": "region"}],
        },
        **extra,
    }


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute("CREATE TABLE main.orders (region VARCHAR, segment VARCHAR, sales DOUBLE)")
    db.executemany("INSERT INTO main.orders VALUES (?, ?, ?)", ORDERS)
    db.execute("CREATE TABLE main.entitlements (who VARCHAR, region VARCHAR)")
    db.executemany("INSERT INTO main.entitlements VALUES (?, ?)", ENTITLEMENTS)
    return db


def regions(
    con: Any, doc: dict[str, Any], session: Session, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    compiled = compile_query(
        doc, query or {"dimensions": ["Region"], "measures": ["Sales"]}, dialect="duckdb", session=session
    )
    assert session.username not in compiled.sql or not session.username  # identities are bind values
    return {r[0]: r[1] for r in con.execute(compiled.sql, list(compiled.parameters)).fetchall()}


ALICE = Session(username="alice", groups=frozenset({"Managers"}))
BOB = Session(username="bob")
CAROL = Session(username="carol", email="carol@example.com")
WENDY = Session(username="wendy", groups=frozenset({"West Team"}))
ERIN = Session(username="erin", groups=frozenset({"Executives"}))
NOBODY = Session(username="nobody")


@pytest.mark.parametrize(
    ("policy", "session", "expected"),
    [
        (entitlement(), ALICE, {"East": 150.0}),
        (entitlement(), BOB, {"East": 150.0, "West": 225.0}),  # several entitlement rows
        (entitlement(), NOBODY, {}),
        (entitlement("email"), CAROL, {"Central": 10.0}),
        (entitlement("email"), BOB, {}),  # no email in the session: nothing
        (entitlement("group"), WENDY, {"West": 225.0}),
        (entitlement("group"), ALICE, {}),
        (
            entitlement(exempt={"groups": ["Executives"]}),
            ERIN,
            {"East": 150.0, "West": 225.0, "Central": 10.0},
        ),
    ],
)
def test_entitlement_personas(
    con: Any, policy: dict[str, Any], session: Session, expected: dict[str, float]
) -> None:
    assert regions(con, model(policy), session) == pytest.approx(expected)


def test_policies_combine_and_apply_before_fixed(con: Any) -> None:
    doc = model(
        entitlement(), {"id": "consumer_only", "filter": "[Segment] = 'Consumer' OR ISMEMBEROF('Finance')"}
    )
    assert regions(con, doc, BOB) == pytest.approx({"East": 100.0, "West": 200.0})
    finance = Session(username="bob", groups=frozenset({"Finance"}))
    assert regions(con, doc, finance) == pytest.approx({"East": 150.0, "West": 225.0})
    # FIXED sees only the rows the viewer may see (RLS is a data source filter in Tableau's order).
    rows = regions(con, doc, BOB, {"dimensions": ["Segment"], "measures": ["Region Total"]})
    assert rows == pytest.approx({"Consumer": 300.0})


def test_identity_values_cannot_inject(con: Any) -> None:
    evil = Session(username="x' OR '1'='1", email="' OR 1=1 --", groups=frozenset({"') OR 1=1 --"}))
    for match_on in ("username", "email", "group"):
        assert regions(con, model(entitlement(match_on)), evil) == {}


def test_unknown_entitlement_table_is_refused() -> None:
    bad = entitlement()
    bad["entitlement"]["table"] = "nope"
    with pytest.raises(CompileError, match="unknown entitlement table"):
        compile_query(model(bad), {"measures": ["Sales"]}, dialect="duckdb", session=ALICE)


@pytest.mark.parametrize("dialect", ["athena", "redshift", "trino"])
def test_entitlement_sql_for_every_dialect(dialect: str) -> None:
    compiled = compile_query(model(entitlement()), {"measures": ["Sales"]}, dialect=dialect, session=ALICE)
    assert "EXISTS" in compiled.sql.upper()
    assert "alice" not in compiled.sql
