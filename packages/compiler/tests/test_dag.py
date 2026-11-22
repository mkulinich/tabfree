from __future__ import annotations

from typing import Any

from tabfree_compiler.model import SemanticModel, analyze_calcs

BASE: dict[str, Any] = {
    "schema": "tabfree/model@0",
    "id": "m",
    "connection": {"engine": "athena", "database": "db"},
    "tables": [{"id": "t", "source": {"table": "t"}, "columns": [{"name": "x", "type": "float"}]}],
    "parameters": [{"id": "p", "name": "P", "type": "integer", "default": 1}],
}


def model(*calcs: tuple[str, str]) -> SemanticModel:
    fields = [{"id": "x", "name": "X", "role": "measure", "type": "float", "table": "t", "column": "x"}]
    fields += [
        {"id": n.lower(), "name": n, "role": "measure", "type": "float", "formula": f} for n, f in calcs
    ]
    return SemanticModel.from_dict({**BASE, "fields": fields})


def test_order_kinds_and_parameters() -> None:
    info = analyze_calcs(
        model(
            ("Ratio", "[Total] / [Count]"),
            ("Total", "SUM([X])"),
            ("Count", "COUNT([X]) + [Parameters].[P]"),
            ("Double", "[X] * 2"),
            ("Fixed", "{FIXED : SUM([Double])}"),
            ("Run", "RUNNING_SUM([Total])"),
        )
    )
    by = {i.name: i for i in info}
    order = [i.name for i in info]
    assert order.index("Total") < order.index("Ratio")
    assert order.index("Count") < order.index("Ratio")
    assert by["Ratio"].kind == "aggregate"  # inherited from its inputs
    assert by["Ratio"].depends_on == ("total", "count")
    assert by["Count"].parameters == ("p",)
    assert by["Double"].kind == "row"
    assert by["Fixed"].kind == "lod"  # the aggregate inside the braces does not leak out
    assert by["Run"].kind == "table_calc"
    assert all(i.error is None for i in info)


def test_cycles_syntax_errors_and_unknown_fields() -> None:
    info = analyze_calcs(
        model(
            ("A", "[B] + 1"),
            ("B", "[A] + 1"),
            ("Bad", "SUM([X]"),
            ("Lost", "[Nope] * 2"),
            ("Uses", "[Bad] + 1"),
        )
    )
    by = {i.name: i for i in info}
    assert by["A"].kind == "invalid"
    assert "circular reference" in (by["A"].error or "")
    assert "syntax" in (by["Bad"].error or "")
    assert by["Lost"].error == "unknown field 'Nope'"
    assert by["Uses"].error == "depends on an invalid calculation"
