"""``tabfree-compile``: compile dashboard queries and calculations, and run the TDVT conformance suite."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from tabfree_compiler.calc import CalcSyntaxError
from tabfree_compiler.model import CompileError, SemanticModel
from tabfree_compiler.sql import DIALECTS, CompiledQuery, Session, compile_expression, compile_query


def _load(path: Path) -> Any:
    from tabfree_schemas import load_document

    return load_document(path)


def _session(args: argparse.Namespace) -> Session:
    today = dt.date.fromisoformat(args.today) if args.today else None
    return Session(username=args.user or "", groups=frozenset(args.group or []), today=today)


def _print(compiled: CompiledQuery) -> None:
    out = {
        "dialect": compiled.dialect,
        "sql": compiled.sql,
        "parameters": list(compiled.parameters) or dict(compiled.named_parameters),
        "columns": [{"name": c.name, "role": c.role, "type": c.type} for c in compiled.columns],
    }
    print(json.dumps(out, indent=2, default=str))


def _query(args: argparse.Namespace) -> int:
    manifest = _load(args.manifest)
    queries = manifest.get("queries", {})
    if args.query not in queries:
        print(f"no query {args.query!r}; choose from {', '.join(sorted(queries))}", file=sys.stderr)
        return 2
    values: dict[str, Any] = {}
    for item in args.param or []:
        name, _, raw = item.partition("=")
        try:
            values[name] = json.loads(raw)  # numbers, true/false, null, "quoted strings"
        except ValueError:
            values[name] = raw
    _print(
        compile_query(
            SemanticModel.from_dict(_load(args.model)),
            queries[args.query],
            dialect=args.dialect,
            session=_session(args),
            parameters=values,
        )
    )
    return 0


def _expr(args: argparse.Namespace) -> int:
    model = SemanticModel.from_dict(_load(args.model))
    _print(compile_expression(model, args.formula, dialect=args.dialect, session=_session(args)))
    return 0


def _tdvt(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError:
        print("the TDVT runner needs DuckDB: install tabfree-compiler[tdvt]", file=sys.stderr)
        return 2
    from tabfree_compiler import tdvt

    root: Path = args.root
    con = duckdb.connect()
    tdvt.load_calcs(con, root / "datasets" / "TestV1" / "Calcs.csv")
    cases = [c for suite in args.suite for c in tdvt.load_suite(root / "exprtests" / suite)]
    results = tdvt.run(cases, con)
    for suite, (passed, total) in tdvt.summarise(results).items():
        print(f"{passed:4d}/{total:<4d} {suite}")
    unexpected = [
        r for r in results if not r.passed and tdvt.deviation_key(r.case) not in tdvt.KNOWN_DEVIATIONS
    ]
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} passed; {len(unexpected)} unexpected failures")
    for r in unexpected[: args.show]:
        print(f"  {r.case.suite}: {r.case.expression}\n    {r.error or r.actual}")
    return 1 if unexpected else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tabfree-compile", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dialect", choices=DIALECTS, default="athena")
        p.add_argument("--user", help="viewer username, for USERNAME() and RLS")
        p.add_argument("--group", action="append", help="viewer group (repeatable), for ISMEMBEROF() and RLS")
        p.add_argument("--today", help="anchor date for relative date filters (YYYY-MM-DD)")

    q = sub.add_parser("query", help="compile a named query of a dashboard manifest")
    q.add_argument("model", type=Path)
    q.add_argument("manifest", type=Path)
    q.add_argument("query")
    q.add_argument("--param", action="append", help="parameter value, name=value (repeatable)")
    common(q)
    q.set_defaults(func=_query)

    e = sub.add_parser("expr", help="compile one calculation as TDVT does")
    e.add_argument("model", type=Path)
    e.add_argument("formula")
    common(e)
    e.set_defaults(func=_expr)

    t = sub.add_parser("tdvt", help="run Tableau's TDVT expression tests on DuckDB")
    t.add_argument("--root", type=Path, default=Path("corpus/tdvt"))
    t.add_argument("--suite", action="append", default=None)
    t.add_argument("--show", type=int, default=20, help="unexpected failures to print")
    t.set_defaults(func=_tdvt)

    args = parser.parse_args(argv)
    if getattr(args, "suite", "unset") is None:
        args.suite = [
            "standard",
            "lodcalcs",
            "median",
            "pretest",
            "regexcalcs",
            "dateparse",
            "percentile",
            "iso8601week",
        ]
    try:
        return int(args.func(args))
    except (CompileError, CalcSyntaxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
