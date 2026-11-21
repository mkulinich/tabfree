from __future__ import annotations

import pytest

from tabfree_api.conformance import main, run
from tabfree_api.engines import DuckDBEngine
from tabfree_compiler import tdvt

pytest.importorskip("duckdb")


def test_runs_suites_through_an_engine() -> None:
    engine = DuckDBEngine.in_memory()
    tdvt.load_calcs(
        engine.connection, tdvt.Path(__file__).resolve().parents[3] / "corpus/tdvt/datasets/TestV1/Calcs.csv"
    )
    report = run(engine, database="main", suites=["lodcalcs", "dateparse"])
    assert report["total"] == 25
    assert report["rate"] == 1.0
    assert report["failures"] == []


def test_cli_fails_under_the_minimum_rate(tmp_path: tdvt.Path) -> None:
    out = tmp_path / "r.json"
    assert main(["--engine", "duckdb", "--suite", "lodcalcs", "--out", str(out)]) == 0
    assert main(["--engine", "duckdb", "--suite", "lodcalcs", "--min-pass", "1.01"]) == 1
    assert out.exists()


def test_loader_sql_is_validated() -> None:
    athena = tdvt.calcs_ddl("athena", location="s3://bucket-name/tdvt/calcs/", database="tdvt")
    assert athena[1].startswith("CREATE EXTERNAL TABLE IF NOT EXISTS tdvt.calcs_raw")
    assert "TRY_CAST" in athena[2]
    with pytest.raises(ValueError, match="S3 location"):
        tdvt.calcs_ddl("athena", location="s3://b/x'; DROP", database="tdvt")
    with pytest.raises(ValueError, match="IAM role"):
        tdvt.calcs_ddl("redshift", location="s3://bucket-name/c.csv", database="tdvt")
