"""Latency probe (BUILD_PLAN P1.16): cold, warm and concurrent timings of real dashboard queries.

The same compiled dashboard queries run on each engine:

- **cold:** each query once, in order (on Athena the first run pays the queueing and planning cost)
- **warm:** each query again, ``--repeat`` times (Athena result reuse and Redshift's result cache apply)
- **concurrent:** every query at once, ``--users`` simulated viewers opening the dashboard together

The probe writes one JSON line per query run, plus a summary with p50 and p95. It needs real AWS
resources (an Athena workgroup over the Iceberg tables, a Redshift Serverless workgroup) and runs only
with explicit credentials in the environment. ``--engine duckdb`` exercises the harness locally.

    uv run python -m tabfree_api.probe --engine duckdb --dashboard sales-analysis
    uv run python -m tabfree_api.probe --engine athena --workgroup tabfree-sandbox --database tabfree_lake
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tabfree_api.engines import AthenaEngine, DuckDBEngine, Engine, RedshiftDataEngine
from tabfree_api.settings import REPO_ROOT
from tabfree_compiler import CompiledQuery, SemanticModel, Session, compile_query
from tabfree_compiler.dashboard import bind_query
from tabfree_compiler.sql import Dialect

EXAMPLES = REPO_ROOT / "packages" / "schemas" / "examples"


@dataclass(frozen=True)
class Timing:
    engine: str
    phase: str
    query: str
    seconds: float
    rows: int
    error: str | None = None


def compile_dashboard(
    manifest: Mapping[str, Any], models: Mapping[str, Mapping[str, Any]], dialect: Dialect
) -> dict[str, CompiledQuery]:
    session = Session(username="probe", groups=frozenset({"Executives"}), today=dt.date(2019, 12, 30))
    state: dict[str, Any] = {"filters": {}, "parameters": {}, "highlight": None}
    out = {}
    for name in manifest["queries"]:
        bound = bind_query(manifest, name, state)
        model = SemanticModel.from_dict(models[bound.model])
        out[name] = compile_query(
            model,
            bound.query,
            dialect=dialect,
            session=session,
            parameters=bound.parameters,
            filters=bound.filters,
        )
    return out


def _timed(engine: Engine, label: str, phase: str, name: str, query: CompiledQuery) -> Timing:
    start = time.perf_counter()
    try:
        rows = len(engine.execute(query, 10_000).rows)
        return Timing(label, phase, name, time.perf_counter() - start, rows)
    except Exception as exc:  # recorded, not raised: one failing query must not stop the probe
        return Timing(label, phase, name, time.perf_counter() - start, 0, f"{type(exc).__name__}: {exc}")


def run_probe(
    engine: Engine,
    label: str,
    queries: Mapping[str, CompiledQuery],
    repeat: int = 3,
    users: int = 10,
    emit: Callable[[Timing], None] = lambda _t: None,
) -> list[Timing]:
    timings: list[Timing] = []

    def record(t: Timing) -> None:
        timings.append(t)
        emit(t)

    for name, query in queries.items():
        record(_timed(engine, label, "cold", name, query))
    for _ in range(repeat):
        for name, query in queries.items():
            record(_timed(engine, label, "warm", name, query))
    jobs = [(name, query) for _ in range(users) for name, query in queries.items()]
    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        for t in pool.map(lambda job: _timed(engine, label, "concurrent", job[0], job[1]), jobs):
            record(t)
    return timings


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def summarise(timings: Sequence[Timing]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for phase in ("cold", "warm", "concurrent"):
        ok = [t.seconds for t in timings if t.phase == phase and t.error is None]
        failed = sum(1 for t in timings if t.phase == phase and t.error is not None)
        out[phase] = {
            "runs": len(ok) + failed,
            "failed": failed,
            "p50_s": round(percentile(ok, 0.5), 3),
            "p95_s": round(percentile(ok, 0.95), 3),
            "mean_s": round(statistics.fmean(ok), 3) if ok else float("nan"),
        }
    return out


def _engine(args: argparse.Namespace) -> Engine:
    if args.engine == "duckdb":
        from tabfree_api.main import _sample_engine

        engine: DuckDBEngine = _sample_engine()
        return engine
    if args.engine == "athena":
        return AthenaEngine(database=args.database, workgroup=args.workgroup, region=args.region)
    return RedshiftDataEngine(
        database=args.database, workgroup=args.workgroup, secret_arn=args.secret_arn, region=args.region
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tabfree-probe", description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", choices=["duckdb", "athena", "redshift"], required=True)
    parser.add_argument(
        "--dashboard", action="append", help="example dashboard id (repeatable; default: all)"
    )
    parser.add_argument("--database", default="tabfree_lake")
    parser.add_argument("--workgroup", default="tabfree-sandbox")
    parser.add_argument("--secret-arn", default=None)
    parser.add_argument("--region", default=None)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--out", type=Path, default=None, help="JSON lines file for every run")
    args = parser.parse_args(argv)

    from tabfree_schemas import load_document

    models = {}
    for path in (EXAMPLES / "models").glob("*.model.yaml"):
        doc = load_document(path)
        models[str(doc["id"])] = doc
    names = args.dashboard or sorted(
        p.name for p in (EXAMPLES / "dashboards").iterdir() if (p / "tabfree.json").is_file()
    )
    engine = _engine(args)
    dialect: Dialect = args.engine
    queries: dict[str, CompiledQuery] = {}
    for name in names:
        manifest = json.loads((EXAMPLES / "dashboards" / name / "tabfree.json").read_text(encoding="utf-8"))
        queries.update({f"{name}/{q}": c for q, c in compile_dashboard(manifest, models, dialect).items()})
    sink = args.out.open("w", encoding="utf-8") if args.out else None
    try:
        timings = run_probe(
            engine,
            args.engine,
            queries,
            repeat=args.repeat,
            users=args.users,
            emit=lambda t: sink.write(json.dumps(asdict(t)) + "\n") if sink else None,
        )
    finally:
        if sink:
            sink.close()
    print(
        json.dumps({"engine": args.engine, "queries": len(queries), "summary": summarise(timings)}, indent=2)
    )
    errors = [t for t in timings if t.error]
    for t in errors[:5]:
        print(f"error in {t.query} ({t.phase}): {t.error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
