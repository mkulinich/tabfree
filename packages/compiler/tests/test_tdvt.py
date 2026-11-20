"""TDVT conformance gate: Tableau's own expected results for calculations and LOD expressions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlglot

from tabfree_compiler import tdvt
from tabfree_compiler.model import CompileError
from tabfree_compiler.sql import compile_expression

duckdb = pytest.importorskip("duckdb")

TDVT = Path(__file__).resolve().parents[3] / "corpus" / "tdvt"
SUITES = ("standard", "lodcalcs", "median", "pretest")
pytestmark = pytest.mark.skipif(not TDVT.exists(), reason="TDVT corpus not present")


@pytest.fixture(scope="module")
def results() -> list[tdvt.Result]:
    con: Any = duckdb.connect()
    tdvt.load_calcs(con, TDVT / "datasets" / "TestV1" / "Calcs.csv")
    cases = [c for suite in SUITES for c in tdvt.load_suite(TDVT / "exprtests" / suite)]
    return tdvt.run(cases, con)


def test_all_tests_pass_except_known_deviations(results: list[tdvt.Result]) -> None:
    unexpected = [
        f"{r.case.suite}: {r.case.expression} -> {r.error or r.actual}"
        for r in results
        if not r.passed and tdvt.deviation_key(r.case) not in tdvt.KNOWN_DEVIATIONS
    ]
    assert unexpected == []
    assert len(results) > 750


def test_known_deviations_still_fail(results: list[tdvt.Result]) -> None:
    """If a deviation starts passing, remove it from the list so the gate stays honest."""
    fixed = [
        key for r in results if r.passed and (key := tdvt.deviation_key(r.case)) in tdvt.KNOWN_DEVIATIONS
    ]
    assert fixed == []
    seen = {tdvt.deviation_key(r.case) for r in results}
    assert set(tdvt.KNOWN_DEVIATIONS) <= seen  # no stale entries


def test_lod_suite_passes_completely(results: list[tdvt.Result]) -> None:
    lod = [r for r in results if r.case.suite.startswith("lodcalcs/")]
    assert lod
    assert all(r.passed for r in lod)


@pytest.mark.parametrize("dialect", ["athena", "redshift", "trino"])
def test_generated_sql_parses_in_each_dialect(results: list[tdvt.Result], dialect: str) -> None:
    model = tdvt.calcs_model()
    read = "trino" if dialect == "athena" else dialect
    checked = 0
    for r in results:
        try:
            compiled = compile_expression(model, r.case.expression, dialect=dialect)  # type: ignore[arg-type]
        except CompileError:
            continue
        sql = compiled.sql if dialect == "redshift" else compiled.sql.replace("?", "NULL")
        sqlglot.parse_one(sql, read=read)
        checked += 1
    assert checked > 750
