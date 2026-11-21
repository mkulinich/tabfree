"""SQL window push-down for table calculations (BUILD_PLAN P2.6, the SQL half of ADR-004).

The JS engine (`@tabfree/tablecalc`) is the source of truth. Pushing a calculation into SQL is an
optimisation for large results, and only for calculations whose SQL form is exactly Tableau's:
running and moving windows, totals, percent of total, differences, ranks (competition, dense,
unique), INDEX, FIRST, LAST, SIZE and LOOKUP. Anything else (custom expressions, secondary
calculations, rank_modified, percentiles, nullIfIncomplete) is left for the runtime.

Addressing follows the runtime: ``computeUsing: specific`` moves along ``addressing`` and partitions
by every other dimension; ``cell`` addresses nothing; every table/pane option addresses all
dimensions in query order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlglot import exp

from tabfree_compiler.model import CompileError

PUSHABLE = frozenset(
    {
        "running_sum",
        "running_avg",
        "running_min",
        "running_max",
        "running_count",
        "window_sum",
        "window_avg",
        "window_min",
        "window_max",
        "window_count",
        "moving_avg",
        "total",
        "percent_of_total",
        "difference",
        "percent_difference",
        "rank",
        "rank_dense",
        "rank_unique",
        "index",
        "first",
        "last",
        "size",
        "lookup",
    }
)


@dataclass(frozen=True)
class PushDown:
    columns: list[tuple[str, exp.Expr]]  # output name, window expression over the inner query "q"
    deferred: list[str]  # table calcs left for the runtime


def _col(name: str) -> exp.Column:
    return exp.Column(this=exp.to_identifier(name, quoted=True), table=exp.to_identifier("q", quoted=True))


def pushable(calc: Mapping[str, Any]) -> bool:
    return (
        calc.get("type") in PUSHABLE
        and not calc.get("then")
        and not calc.get("nullIfIncomplete")
        and not calc.get("orderBy")
        and not calc.get("atLevel")
        and not calc.get("restartEvery")
    )


def _frame(start: int | str | None, end: int | str | None) -> exp.WindowSpec:
    def side(value: int | str | None, which: str) -> tuple[exp.Expr | str, str]:
        if value is None or value in ("first", "last"):
            return "UNBOUNDED", "PRECEDING" if which == "start" else "FOLLOWING"
        if not isinstance(value, int):
            raise CompileError(f"invalid window bound {value!r}")
        if value == 0:
            return "CURRENT ROW", ""
        return exp.Literal.number(abs(value)), "PRECEDING" if value < 0 else "FOLLOWING"

    s, s_side = side(start, "start")
    e, e_side = side(end, "end")
    return exp.WindowSpec(kind="ROWS", start=s, start_side=s_side or None, end=e, end_side=e_side or None)


def push_down(
    calcs: Sequence[Mapping[str, Any]], dimensions: Sequence[str], measures: Sequence[str]
) -> PushDown:
    """Window expressions for the pushable calcs, evaluated over the query's result aliased ``q``."""
    out: list[tuple[str, exp.Expr]] = []
    deferred: list[str] = []
    available = set(dimensions) | set(measures)
    for calc in calcs:
        name = str(calc["name"])
        if not pushable(calc) or (calc.get("of") is not None and calc["of"] not in available):
            deferred.append(name)
            continue
        using = calc.get("computeUsing", "table_down")
        if using == "specific":
            addressing = list(calc.get("addressing", []))
        elif using == "cell":
            addressing = []
        else:
            addressing = list(dimensions)
        partitioning = list(calc.get("partitioning") or [d for d in dimensions if d not in addressing])
        if any(a not in dimensions for a in addressing + partitioning):
            deferred.append(name)
            continue
        part = [_col(p) for p in partitioning]
        order = exp.Order(expressions=[exp.Ordered(this=_col(a), nulls_first=True) for a in addressing])

        def over(
            fn: exp.Expr,
            ordered: bool = True,
            frame: exp.WindowSpec | None = None,
            part: list[exp.Column] = part,
            order: exp.Order = order,
            addressing: list[str] = addressing,
        ) -> exp.Window:
            return exp.Window(
                this=fn,
                partition_by=[p.copy() for p in part] if part else None,
                order=order.copy() if ordered and addressing else None,
                spec=frame,
            )

        kind = calc["type"]
        value: exp.Expr = _col(str(calc["of"])) if calc.get("of") is not None else exp.Literal.number(1)
        agg = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max, "count": exp.Count}
        expr: exp.Expr
        if kind.startswith("running_"):
            fn = agg[kind.removeprefix("running_")](this=value)
            expr = over(fn, frame=_frame("first", 0))
        elif kind.startswith("window_") or kind == "moving_avg":
            frame = calc.get("frame") or {}
            start = frame.get("start", -2 if kind == "moving_avg" else "first")
            end = frame.get("end", 0 if kind == "moving_avg" else "last")
            fn = agg["avg" if kind == "moving_avg" else kind.removeprefix("window_")](this=value)
            expr = over(fn, frame=_frame(start, end))
        elif kind == "total":
            expr = over(exp.Sum(this=value), ordered=False)
        elif kind == "percent_of_total":
            total = over(exp.Sum(this=value.copy()), ordered=False)
            expr = exp.Div(
                this=exp.Cast(this=value, to=exp.DataType.build("DOUBLE")),
                expression=exp.Nullif(this=total, expression=exp.Literal.number(0)),
            )
        elif kind in ("difference", "percent_difference", "lookup"):
            relative = calc.get("relativeTo", "previous")
            if kind == "lookup":
                offset = int(calc.get("offset", 0))
                ref: exp.Expr = (
                    value.copy()
                    if offset == 0
                    else over(
                        exp.Lag(this=value.copy(), offset=exp.Literal.number(-offset))
                        if offset < 0
                        else exp.Lead(this=value.copy(), offset=exp.Literal.number(offset))
                    )
                )
                out.append((name, ref))
                continue
            if relative == "previous":
                ref = over(exp.Lag(this=value.copy(), offset=exp.Literal.number(1)))
            elif relative == "next":
                ref = over(exp.Lead(this=value.copy(), offset=exp.Literal.number(1)))
            elif relative == "first":
                ref = over(exp.FirstValue(this=value.copy()), frame=_frame("first", "last"))
            else:
                ref = over(exp.LastValue(this=value.copy()), frame=_frame("first", "last"))
            diff = exp.Sub(this=value, expression=ref)
            if kind == "difference":
                expr = diff
            else:
                expr = exp.Div(
                    this=exp.Cast(this=exp.Paren(this=diff), to=exp.DataType.build("DOUBLE")),
                    expression=exp.Nullif(this=exp.Abs(this=ref.copy()), expression=exp.Literal.number(0)),
                )
        elif kind in ("rank", "rank_dense", "rank_unique"):
            desc = calc.get("direction", "desc") == "desc"
            rank_fn: exp.Expr = (
                exp.RowNumber()
                if kind == "rank_unique"
                else exp.Anonymous(this="RANK" if kind == "rank" else "DENSE_RANK", expressions=[])
            )
            # NULLs get no rank (Tableau), so they are partitioned away from the ranked values.
            window = exp.Window(
                this=rank_fn,
                partition_by=[*part, exp.Is(this=value.copy(), expression=exp.Null())],
                order=exp.Order(expressions=[exp.Ordered(this=value.copy(), desc=desc)]),
            )
            expr = exp.Case().when(exp.Is(this=value, expression=exp.Null()), exp.Null()).else_(window)
        elif kind == "index":
            expr = over(exp.RowNumber())
        elif kind == "size":
            expr = over(exp.Count(this=exp.Star()), ordered=False)
        elif kind == "first":
            expr = exp.Sub(this=exp.Literal.number(1), expression=over(exp.RowNumber()))
        else:  # last
            expr = exp.Sub(
                this=over(exp.Count(this=exp.Star()), ordered=False), expression=over(exp.RowNumber())
            )
        out.append((name, expr))
    return PushDown(out, deferred)
