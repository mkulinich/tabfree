from __future__ import annotations

import json
from typing import Any

import pytest

from tabfree_api.engines import EngineError, EngineResult
from tabfree_api.probe import EXAMPLES, compile_dashboard, main, percentile, run_probe, summarise
from tabfree_compiler import CompiledQuery
from tabfree_schemas import load_document


class FakeEngine:
    dialect = "athena"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, query: CompiledQuery, max_rows: int) -> EngineResult:
        self.calls += 1
        if "FAIL" in query.sql:
            raise EngineError("boom")
        return EngineResult([[1], [2]])


def models() -> dict[str, Any]:
    out = {}
    for path in (EXAMPLES / "models").glob("*.model.yaml"):
        doc = load_document(path)
        out[doc["id"]] = doc
    return out


@pytest.mark.parametrize("dialect", ["athena", "redshift", "duckdb"])
def test_every_example_dashboard_compiles_for_the_probe(dialect: str) -> None:
    for folder in (EXAMPLES / "dashboards").iterdir():
        manifest = json.loads((folder / "tabfree.json").read_text(encoding="utf-8"))
        compiled = compile_dashboard(manifest, models(), dialect)
        assert set(compiled) == set(manifest["queries"])


def test_phases_counts_and_errors() -> None:
    engine = FakeEngine()
    ok = CompiledQuery("SELECT 1", "athena", (), {}, ())
    bad = CompiledQuery("SELECT FAIL", "athena", (), {}, ())
    timings = run_probe(engine, "fake", {"a": ok, "b": bad}, repeat=2, users=3)  # type: ignore[arg-type]
    assert engine.calls == 2 + 4 + 6
    summary = summarise(timings)
    assert summary["cold"]["runs"] == 2
    assert summary["cold"]["failed"] == 1
    assert summary["concurrent"]["runs"] == 6
    assert percentile([3.0, 1.0, 2.0], 0.5) == 2.0


def test_local_run_writes_json_lines(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("duckdb")
    out = tmp_path / "runs.jsonl"
    assert (
        main(
            [
                "--engine",
                "duckdb",
                "--dashboard",
                "sales-overview",
                "--repeat",
                "1",
                "--users",
                "2",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {line["phase"] for line in lines} == {"cold", "warm", "concurrent"}
    assert json.loads(capsys.readouterr().out)["queries"] == 4
