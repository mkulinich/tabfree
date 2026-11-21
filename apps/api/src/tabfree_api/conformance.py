"""Conformance runs on real engines (BUILD_PLAN P2.9): Tableau's TDVT expression suites on Athena and
Redshift, through the same engines the query service uses.

    uv run python -m tabfree_api.conformance --engine athena --workgroup tabfree-ci \\
        --database tdvt --load s3://BUCKET/tdvt/calcs/ --out tdvt-athena.json
    uv run python -m tabfree_api.conformance --engine redshift --workgroup tabfree-ci \\
        --database dev --schema tdvt --load s3://BUCKET/tdvt/calcs/Calcs.csv --iam-role arn:aws:iam::…

``--load`` creates the Calcs table first (upload ``corpus/tdvt/datasets/TestV1/Calcs.csv`` there).
The run fails when the pass rate is below ``--min-pass`` (M2 requires 95%). ``--engine duckdb``
exercises the same path locally. Needs AWS credentials in the environment for the AWS engines.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tabfree_api.engines import AthenaEngine, DuckDBEngine, Engine, RedshiftDataEngine
from tabfree_api.settings import REPO_ROOT
from tabfree_compiler import tdvt
from tabfree_compiler.sql import CompiledQuery, Dialect

SUITES = ("standard", "lodcalcs", "median", "pretest", "regexcalcs", "dateparse", "percentile", "iso8601week")
TDVT_ROOT = REPO_ROOT / "corpus" / "tdvt"


def _statement(sql: str, dialect: Dialect) -> CompiledQuery:
    return CompiledQuery(sql=sql, dialect=dialect, parameters=(), named_parameters={}, columns=())


def run(
    engine: Engine,
    *,
    database: str,
    suites: Sequence[str] = SUITES,
    load: str | None = None,
    iam_role: str | None = None,
    root: Path = TDVT_ROOT,
) -> dict[str, Any]:
    dialect = engine.dialect
    if load:
        for sql in tdvt.calcs_ddl(dialect, location=load, database=database, iam_role=iam_role):
            engine.execute(_statement(sql, dialect), 0)
    cases = [c for suite in suites for c in tdvt.load_suite(root / "exprtests" / suite)]
    results = tdvt.run_with(
        cases,
        lambda compiled: engine.execute(compiled, 100_000).rows,
        dialect,
        tdvt.calcs_model(database),
        # DuckDB lacks some ICU regex features; the AWS engines run every suite.
        skip=tdvt.ENGINE_SPECIFIC if dialect == "duckdb" else None,
    )
    passed = sum(r.passed for r in results)
    unexpected = [
        r for r in results if not r.passed and tdvt.deviation_key(r.case) not in tdvt.KNOWN_DEVIATIONS
    ]
    return {
        "engine": dialect,
        "passed": passed,
        "total": len(results),
        "rate": passed / len(results) if results else 0.0,
        "suites": {k: {"passed": v[0], "total": v[1]} for k, v in tdvt.summarise(results).items()},
        "failures": [
            {"suite": r.case.suite, "expression": r.case.expression, "error": r.error, "actual": r.actual[:5]}
            for r in unexpected
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tabfree-conformance", description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", choices=["duckdb", "athena", "redshift"], required=True)
    parser.add_argument("--database", default="tdvt", help="Athena database, or Redshift schema for Calcs")
    parser.add_argument("--redshift-database", default="dev")
    parser.add_argument("--workgroup", default="primary")
    parser.add_argument("--secret-arn", default=None)
    parser.add_argument("--region", default=None)
    parser.add_argument("--load", default=None, help="S3 location of Calcs.csv to load first")
    parser.add_argument("--iam-role", default=None, help="Redshift COPY role")
    parser.add_argument("--suite", action="append", default=None)
    parser.add_argument("--min-pass", type=float, default=0.95)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    engine: Engine
    database = args.database
    if args.engine == "duckdb":
        local = DuckDBEngine.in_memory()
        tdvt.load_calcs(local.connection, TDVT_ROOT / "datasets" / "TestV1" / "Calcs.csv")
        engine, database = local, "main"
    elif args.engine == "athena":
        engine = AthenaEngine(database=database, workgroup=args.workgroup, region=args.region)
    else:
        engine = RedshiftDataEngine(
            database=args.redshift_database,
            workgroup=args.workgroup,
            secret_arn=args.secret_arn,
            region=args.region,
        )
    report = run(
        engine, database=database, suites=args.suite or SUITES, load=args.load, iam_role=args.iam_role
    )
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"{report['engine']}: {report['passed']}/{report['total']} ({report['rate']:.1%})")
    for failure in report["failures"][:20]:
        print(
            f"  {failure['suite']}: {failure['expression']} -> {failure['error'] or failure['actual']}",
            file=sys.stderr,
        )
    return 0 if report["rate"] >= args.min_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
