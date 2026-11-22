from __future__ import annotations

import json
from pathlib import Path

import pytest

from tabfree_compiler.calc import (
    Binary,
    CalcSyntaxError,
    Call,
    Case,
    Field,
    If,
    InList,
    Lit,
    Lod,
    Unary,
    classify,
    parse,
    to_formula,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_precedence_follows_tableau() -> None:
    # Unary minus binds tighter than ^; * before +; comparison before NOT, AND, OR.
    assert parse("-2^2") == Binary("^", Lit("integer", -2), Lit("integer", 2))
    assert parse("1 + 2 * 3") == Binary(
        "+", Lit("integer", 1), Binary("*", Lit("integer", 2), Lit("integer", 3))
    )
    node = parse("NOT [a] = 1 AND [b] OR [c]")
    assert node == Binary(
        "OR",
        Binary("AND", Unary("NOT", Binary("=", Field(("a",)), Lit("integer", 1))), Field(("b",))),
        Field(("c",)),
    )


def test_literals_fields_and_comments() -> None:
    assert parse("'it''s'") == Lit("string", "it's")
    assert parse('"say ""hi"""') == Lit("string", 'say "hi"')
    assert parse("#2024-01-31#") == Lit("date", "2024-01-31")
    assert parse("1.5e3") == Lit("float", 1500.0)
    assert parse("[Sales ]] (USD)]") == Field(("Sales ] (USD)",))
    assert parse("[Parameters].[Top N]") == Field(("Parameters", "Top N"))
    assert parse("num0 // trailing comment") == Field(("num0",))
    assert parse("/* block */ TRUE") == Lit("boolean", True)
    assert parse("null") == Lit("null", None)


def test_control_flow_and_lists() -> None:
    node = parse("IF [x] > 0 THEN 'pos' ELSEIF [x] < 0 THEN 'neg' ELSE 'zero' END")
    assert isinstance(node, If)
    assert len(node.branches) == 2
    assert node.otherwise == Lit("string", "zero")
    # ELSE IF is a nested IF with its own END, not ELSEIF.
    nested = parse("IF [a] THEN 1 ELSE IF [b] THEN 2 END END")
    assert isinstance(nested, If)
    assert isinstance(nested.otherwise, If)
    case = parse("CASE [r] WHEN 'East' THEN 1 WHEN 'West' THEN 2 END")
    assert isinstance(case, Case)
    assert case.otherwise is None
    assert len(case.whens) == 2
    assert parse("[r] IN ('a', 'b')") == InList(Field(("r",)), (Lit("string", "a"), Lit("string", "b")))
    assert parse("sum([x])") == Call("SUM", (Field(("x",)),))


def test_lod_expressions() -> None:
    node = parse("{FIXED [Region], [State] : SUM([Sales])}")
    assert node == Lod("fixed", (Field(("Region",)), Field(("State",))), Call("SUM", (Field(("Sales",)),)))
    assert parse("{SUM([Sales])}") == Lod(None, (), Call("SUM", (Field(("Sales",)),)))
    assert parse("{exclude [a] : AVG([b])}").scope == "exclude"  # type: ignore[union-attr]


def test_classification() -> None:
    assert classify(parse("[a] + 1")) == "row"
    assert classify(parse("SUM([a]) / SUM([b])")) == "aggregate"
    assert classify(parse("{FIXED [c] : SUM([a])}")) == "lod"
    assert classify(parse("AVG({FIXED [c] : SUM([a])})")) == "aggregate"
    assert classify(parse("RUNNING_SUM(SUM([a]))")) == "table_calc"
    assert classify(parse("MIN([a], [b])")) == "row"  # two-argument MIN is row-level


@pytest.mark.parametrize(
    "formula",
    ["", "1 +", "IF [a] THEN 1", "CASE [a] END", "SUM([a]", "{FIXED [a] SUM([b])}", "[a] $ 1", "'open"],
)
def test_syntax_errors(formula: str) -> None:
    with pytest.raises(CalcSyntaxError):
        parse(formula)


def test_deep_nesting_is_refused() -> None:
    with pytest.raises(CalcSyntaxError, match="nested too deeply"):
        parse("(" * 500 + "1" + ")" * 500)


def test_to_formula_round_trips() -> None:
    for formula in (
        "IF [a] > 1 THEN 'x' ELSE 'y' END",
        "{INCLUDE [c] : AVG([a] * 2)}",
        "CASE [r] WHEN 'a''b' THEN NULL END",
        "NOT ([x] IN (1, 2.5, #2024-01-01#))",
    ):
        node = parse(formula)
        assert parse(to_formula(node)) == node


def test_every_corpus_formula_parses() -> None:
    twb = pytest.importorskip("tabfree_migrate.twb")
    manifest = REPO_ROOT / "corpus" / "manifest.json"
    if not manifest.exists():
        pytest.skip("corpus not present")
    count = 0
    for entry in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
        if entry.get("kind") not in {"twb", "twbx", "tds", "tdsx"} or entry.get("expect_invalid"):
            continue
        ir = twb.parse_file(REPO_ROOT / entry["path"])
        for ds in ir.get("datasources", []):
            for column in ds.get("columns", []):
                formula = (column.get("calculation") or {}).get("formula") or column.get("formula")
                if formula:
                    parse(formula)
                    count += 1
    assert count > 1500
