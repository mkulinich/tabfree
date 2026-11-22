"""Compile semantic queries to SQL with Tableau's semantics, on SQLGlot (ADR-003).

Plan shape, following Tableau's order of operations::

    src       every column the query needs, from the model's tables and many-to-one joins
    base      src after data source filters, row-level security and context filters
    filtered  base after dimension filters (Top N and condition filters are computed on base)
    agg_*     aggregates at a grain; FIXED LODs read base, INCLUDE/EXCLUDE read the current source
    final     dimensions and measures at the view grain, then measure filters, sort and limit

LOD values are joined at the combined grain of the view and the LOD (a distinct domain), so an LOD that
is coarser than the view replicates and one that is finer is aggregated, as in Tableau. An LOD used inside
a row-level expression that also reads columns is joined per row.

Viewer identity (``USERNAME()``, ``ISMEMBEROF()``...), parameters and filter values are bind parameters,
never SQL text. Identifiers come from the model, are validated, and are always quoted.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlglot
from sqlglot import exp

from tabfree_compiler.calc import (
    AGGREGATES,
    TABLE_CALCS,
    Binary,
    Call,
    Case,
    Field,
    If,
    InList,
    Lit,
    Lod,
    Node,
    Unary,
    children,
    is_aggregate_call,
    parse,
    to_formula,
    walk,
)
from tabfree_compiler.model import CompileError, SemanticModel, UnsupportedError, measure_node

Dialect = Literal["athena", "redshift", "trino", "duckdb"]
DIALECTS: tuple[Dialect, ...] = ("athena", "redshift", "trino", "duckdb")

_NUMERIC = {"integer", "float", "decimal"}
_TEMPORAL = {"date", "datetime"}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DATE_PARTS = {
    "year",
    "quarter",
    "month",
    "week",
    "weekday",
    "day",
    "dayofyear",
    "hour",
    "minute",
    "second",
    "iso_year",
    "iso_quarter",
    "iso_week",
    "iso_weekday",
}


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """Who is asking. The governed query service builds this from the signed-in user."""

    username: str = ""
    fullname: str = ""
    domain: str = ""
    email: str = ""
    groups: frozenset[str] = frozenset()
    attributes: Mapping[str, str] = field(default_factory=dict)
    today: dt.date | None = None  # anchor for relative dates; None means the engine's current date


@dataclass(frozen=True)
class OutputColumn:
    name: str
    role: Literal["dimension", "measure"]
    type: str


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    dialect: Dialect
    #: Positional values for ``?`` placeholders (athena, trino, duckdb).
    parameters: tuple[Any, ...]
    #: Named values for ``:name`` placeholders (redshift Data API).
    named_parameters: Mapping[str, Any]
    columns: tuple[OutputColumn, ...]
    #: Table calculations left for the runtime (with ``table_calcs="sql"``, the ones SQL cannot do).
    deferred_table_calcs: tuple[str, ...] = ()


def compile_query(
    model: SemanticModel | Mapping[str, Any],
    query: Mapping[str, Any],
    *,
    dialect: Dialect = "athena",
    session: Session | None = None,
    parameters: Mapping[str, Any] | None = None,
    filters: Sequence[Mapping[str, Any]] = (),
    table_calcs: Literal["runtime", "sql"] = "runtime",
) -> CompiledQuery:
    """Compile one named query of a dashboard manifest (``tabfree.json``) against a semantic model.

    ``filters`` are runtime filters in the manifest's ``queryFilter`` shape (dashboard filters with the
    viewer's current values); ``parameters`` maps parameter names or ids to values.
    """
    if dialect not in DIALECTS:
        raise CompileError(f"unknown dialect {dialect!r}")
    if not isinstance(model, SemanticModel):
        model = SemanticModel.from_dict(model)
    # Table calculations (query["tableCalcs"]) run in the runtime's JS engine over this result (ADR-004).
    planner = _Planner(model, dialect, session or Session(), parameters or {})
    planner.table_calc_mode = table_calcs
    return planner.compile(query, list(query.get("where", [])) + list(filters))


def compile_expression(
    model: SemanticModel | Mapping[str, Any],
    formula: str,
    *,
    dialect: Dialect = "athena",
    session: Session | None = None,
    as_dimension: bool | None = None,
) -> CompiledQuery:
    """Compile a single calculation as a one-column query: distinct values if it is row-level,
    one row if it is an aggregate. This is how TDVT evaluates expressions."""
    if not isinstance(model, SemanticModel):
        model = SemanticModel.from_dict(model)
    node = model.expand(parse(formula))
    aggregate = any(is_aggregate_call(n) for n in _outside_lods(node))
    if as_dimension is None:
        as_dimension = not aggregate
    key = "dimensions" if as_dimension else "measures"
    return compile_query(model, {key: [{"expr": formula, "as": "value"}]}, dialect=dialect, session=session)


# ---------------------------------------------------------------------------------------------
# Typed SQL fragments
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class T:
    e: exp.Expr
    type: str  # string integer float decimal boolean date datetime null unknown


def _sql_type(type_: str) -> exp.DataType:
    name = {
        "string": "VARCHAR",
        "integer": "BIGINT",
        "float": "DOUBLE",
        "decimal": "DOUBLE",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    }.get(type_)
    if name is None:
        raise CompileError(f"no SQL type for {type_!r}")
    return exp.DataType.build(name)


def _cast(e: exp.Expr, type_: str) -> exp.Expr:
    return exp.Cast(this=e, to=_sql_type(type_))


def _num(n: int | float) -> exp.Expr:
    lit = exp.Literal.number(abs(n))
    return exp.Neg(this=lit) if n < 0 else lit


def _str(s: str) -> exp.Expr:
    return exp.Literal.string(s)


def _paren(e: exp.Expr) -> exp.Expr:
    return e if isinstance(e, exp.Column | exp.Literal | exp.Paren | exp.Placeholder) else exp.Paren(this=e)


def _case(whens: Sequence[tuple[exp.Expr, exp.Expr]], default: exp.Expr | None) -> exp.Case:
    case = exp.Case()
    for cond, value in whens:
        case = case.when(cond, value)
    if default is not None:
        case = case.else_(default)
    return case


def _null_safe_eq(a: exp.Expr, b: exp.Expr) -> exp.Expr:
    # Portable IS NOT DISTINCT FROM (Redshift lacks it).
    return exp.Paren(
        this=exp.or_(
            exp.EQ(this=a, expression=b),
            exp.and_(exp.Is(this=a, expression=exp.Null()), exp.Is(this=b.copy(), expression=exp.Null())),
        )
    )


def _ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def _col(table: str | None, name: str) -> exp.Column:
    return exp.Column(this=_ident(name), table=_ident(table) if table else None)


def _field_ref(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _validated(name: str, what: str) -> str:
    if not _IDENTIFIER.match(name):
        raise CompileError(f"invalid {what} identifier {name!r}")
    return name


def _outside_lods(node: Node) -> list[Node]:
    """Nodes not inside an LOD (the LOD node itself is included)."""
    out = [node]
    if not isinstance(node, Lod):
        for child in children(node):
            out.extend(_outside_lods(child))
    return out


def _top_lods(node: Node) -> list[Lod]:
    return [n for n in _outside_lods(node) if isinstance(n, Lod)]


# ---------------------------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------------------------


@dataclass
class _RowScope:
    """A relation rows are read from, plus the LOD tables joined onto it."""

    relation: str  # CTE name
    alias: str
    source: str  # the CTE that INCLUDE/EXCLUDE LODs aggregate ("base" or "filtered")
    grain: tuple[Node, ...]  # view grain, for INCLUDE/EXCLUDE
    keymap: dict[Node, str] = field(default_factory=dict)  # precomputed dimension columns
    joins: list[exp.Join] = field(default_factory=list)
    lod_cache: dict[tuple[Lod, tuple[Node, ...]], str] = field(default_factory=dict)


@dataclass
class _Cte:
    name: str
    select: exp.Select


class _Planner:
    def __init__(
        self, model: SemanticModel, dialect: Dialect, session: Session, params: Mapping[str, Any]
    ) -> None:
        self.model = model
        self.dialect = dialect
        self.session = session
        self.param_values = {k.casefold(): v for k, v in params.items()}
        self.nonce = secrets.token_hex(4)
        self.binds: dict[str, Any] = {}
        self.ctes: list[_Cte] = []
        self.columns: dict[str, str] = {}  # field id -> src column alias
        self.tables_used: set[str] = set()
        self.agg_cache: dict[tuple[str, tuple[Node, ...], tuple[Node, ...]], str] = {}
        self.week_start = _WEEKDAYS.index(model.week_start) if model.week_start in _WEEKDAYS else 6
        self.counter = 0
        self.table_calc_mode: Literal["runtime", "sql"] = "runtime"
        self.root_table: str | None = None
        self.pk_columns: dict[str, list[str]] = {}
        self.native_atoms: set[Call] = set()

    # -- entry point ---------------------------------------------------------------------------
    def compile(self, query: Mapping[str, Any], where: list[Mapping[str, Any]]) -> CompiledQuery:
        dims = [self._dimension(d) for d in query.get("dimensions", [])]
        measures = [self._measure(m) for m in query.get("measures", [])]
        names = [n for n, _, _ in dims] + [n for n, _ in measures]
        if len(set(names)) != len(names):
            raise CompileError("output names must be unique within a query")
        # Computed sorts ("sort Customer by SUM(Sales)") aggregate a hidden measure.
        hidden: list[tuple[str, Node]] = []
        for i, spec in enumerate(query.get("sort", [])):
            if spec.get("expr"):
                node = self.model.expand_formula(spec["expr"])
                if not any(is_aggregate_call(n) for n in _outside_lods(node)):
                    node = Call("SUM", (node,))
                hidden.append((f"__sort{i}", node))
        visible_measures = measures
        measures = measures + hidden

        context, dimension_filters, measure_filters = self._split_filters(where)
        rls = self._rls_conditions()
        everything: list[Node] = [n for _, n, _ in dims] + [n for _, n in measures]
        everything += [f for f, _ in context + dimension_filters + measure_filters]
        for spec in where:  # formulas inside Top N and condition filters read columns too
            if spec.get("condition"):
                everything.append(self.model.expand_formula(spec["condition"]))
            if spec.get("topN"):
                everything.append(self.model.expand_formula(spec["topN"]["by"]))
        everything += rls + [self.model.expand_formula(f) for f in self.model.data_source_filters]
        entitlements = self._entitlements()
        for ent in entitlements:
            everything += [self.model.expand_formula(_field_ref(name)) for name, _ in ent.keys]
        for node in everything:
            self._register_columns(node)
        self._build_src()

        # base: data source filters, RLS and context filters, in that order (all before FIXED).
        base_scope = self._scope("src", "base_src", "src", ())
        conditions = [
            self._row(self.model.expand_formula(f), base_scope).e for f in self.model.data_source_filters
        ]
        conditions += [self._row(n, base_scope).e for n in rls]
        conditions += [self._entitlement(ent, i, base_scope) for i, ent in enumerate(entitlements)]
        conditions += [self._filter_condition(n, spec, base_scope) for n, spec in context]
        self._add_cte("base", self._select_star(base_scope, conditions))

        grain = tuple(n for _, n, _ in dims)
        filtered_scope = self._scope("base", "b", "base", grain)
        conditions = [self._filter_condition(n, spec, filtered_scope) for n, spec in dimension_filters]
        self._add_cte("filtered", self._select_star(filtered_scope, conditions))

        result = self._aggregate("filtered", grain, tuple(n for _, n in measures))
        out = exp.select(
            *[exp.alias_(_col("r", f"k{i}"), name, quoted=True) for i, (name, _, _) in enumerate(dims)],
            *[exp.alias_(_col("r", f"m{i}"), name, quoted=True) for i, (name, _) in enumerate(measures)],
        ).from_(exp.Table(this=_ident(result), alias=exp.TableAlias(this=_ident("r"))))

        if measure_filters:
            mf = self._aggregate("filtered", grain, tuple(n for n, _ in measure_filters))
            if grain:
                on = exp.and_(
                    *[_null_safe_eq(_col("r", f"k{i}"), _col("mf", f"k{i}")) for i in range(len(grain))]
                )
                out = out.join(
                    exp.Table(this=_ident(mf), alias=exp.TableAlias(this=_ident("mf"))),
                    on=on,
                    join_type="inner",
                )
            else:
                out = out.join(
                    exp.Table(this=_ident(mf), alias=exp.TableAlias(this=_ident("mf"))), join_type="cross"
                )
            out = out.where(exp.and_(*[_col("mf", f"m{i}") for i in range(len(measure_filters))]))

        out, columns_extra = self._finish(query, out, dims, measures, hidden, names)
        sort = [x for x in query.get("sort", []) if not x.get("expr")]

        calcs = list(query.get("tableCalcs", []))
        deferred = [str(c["name"]) for c in calcs]
        pushed: list[tuple[str, exp.Expr]] = []
        if calcs and self.table_calc_mode == "sql":
            from tabfree_compiler.windows import push_down

            plan = push_down(calcs, [n for n, _, _ in dims], [n for n, _ in visible_measures])
            pushed, deferred = plan.columns, plan.deferred
            if pushed:
                wrapper = exp.select(
                    exp.Column(this=exp.Star(), table=_ident("q")),
                    *[exp.alias_(e, n, quoted=True) for n, e in pushed],
                ).from_(out.subquery("q"))
                if sort:
                    wrapper = wrapper.order_by(
                        *[
                            exp.Ordered(
                                this=_col(None, s["by"]), desc=s.get("dir") == "desc", nulls_first=False
                            )
                            for s in sort
                        ]
                    )
                out = wrapper
        pivot = bool(query.get("pivotMeasures"))
        if pivot:
            out = self._pivot(out, dims, visible_measures, columns_extra)
            columns_extra = [
                *columns_extra,
                OutputColumn("Measure Names", "dimension", "string"),
                OutputColumn("Measure Values", "measure", "float"),
            ]
        for cte in self.ctes:
            out = out.with_(cte.name, as_=cte.select)
        compiled = _render(self, out, dims, [] if pivot else visible_measures)
        extra = tuple(OutputColumn(n, "measure", "float") for n, _ in pushed)
        return dataclasses.replace(
            compiled,
            columns=compiled.columns + tuple(columns_extra) + extra,
            deferred_table_calcs=tuple(deferred),
        )

    # -- query tail: sorts, limit, totals ------------------------------------------------------
    def _finish(
        self,
        query: Mapping[str, Any],
        out: exp.Select,
        dims: list[tuple[str, Node, str]],
        measures: list[tuple[str, Node]],
        hidden: list[tuple[str, Node]],
        names: list[str],
    ) -> tuple[exp.Select, list[OutputColumn]]:
        sort = list(query.get("sort", []))
        order: list[exp.Ordered] = []
        for i, spec in enumerate(sort):
            if spec["by"] not in names:
                raise CompileError(f"sort by unknown output {spec['by']!r}")
            if spec.get("order"):
                # Manual sort: listed members first, in the listed order; the rest after, ascending.
                members = list(spec["order"])
                case = exp.Case(
                    ifs=[
                        exp.If(
                            this=exp.EQ(this=_col(None, spec["by"]), expression=exp.convert(m)),
                            true=exp.convert(i),
                        )
                        for i, m in enumerate(members)
                        if m is not None
                    ],
                    default=exp.convert(len(members)),
                )
                order.append(exp.Ordered(this=case, nulls_first=False))
                order.append(exp.Ordered(this=_col(None, spec["by"]), nulls_first=False))
                continue
            key = f"__sort{i}" if spec.get("expr") else spec["by"]
            order.append(exp.Ordered(this=_col(None, key), desc=spec.get("dir") == "desc", nulls_first=False))
        if order:
            out = out.order_by(*order)
        if query.get("limit"):
            out = out.limit(int(query["limit"]))
        shown = [(n, node) for n, node in measures if not n.startswith("__sort")]
        visible = [n for n, _, _ in dims] + [n for n, _ in shown]
        extra: list[OutputColumn] = []

        totals = query.get("totals") or {}
        dim_names = [n for n, _, _ in dims]
        levels: list[tuple[str, int]] = []  # (label, number of leading dimensions kept)
        for sub in totals.get("subtotals", []):
            if sub not in dim_names:
                raise CompileError(f"subtotal on unknown dimension {sub!r}")
            levels.append((sub, dim_names.index(sub) + 1))
        if totals.get("grand"):
            levels.append(("grand", 0))
        if levels:
            # Totals are re-aggregated at their own grain (COUNTD, AVG and LODs stay right), never summed.
            detail = exp.select(
                *[_col("d", n) for n in visible],
                exp.alias_(_cast(exp.Null(), "string"), "__total", quoted=True),
            ).from_(out.subquery("d"))
            union: exp.Query = detail
            for label, keep in levels:
                grain = tuple(node for _, node, _ in dims[:keep])
                agg = self._aggregate("filtered", grain, tuple(node for _, node in shown))
                cols: list[exp.Expr] = []
                for i, (name, _, _) in enumerate(dims):
                    value = _col("t", f"k{i}") if i < keep else exp.Null()
                    cols.append(exp.alias_(value, name, quoted=True))
                for j, (name, _) in enumerate(shown):
                    cols.append(exp.alias_(_col("t", f"m{j}"), name, quoted=True))
                cols.append(exp.alias_(_str(label), "__total", quoted=True))
                total = exp.select(*cols).from_(
                    exp.Table(this=_ident(agg), alias=exp.TableAlias(this=_ident("t")))
                )
                union = exp.union(union, total, distinct=False)
            ordering = [
                exp.Ordered(this=exp.Not(this=exp.Is(this=_col(None, "__total"), expression=exp.Null())))
            ]
            ordering += [
                exp.Ordered(this=_col(None, x["by"]), desc=x.get("dir") == "desc", nulls_first=False)
                for x in sort
                if not x.get("expr")
            ]
            out = exp.select(exp.Star()).from_(union.subquery("u")).order_by(*ordering)
            extra.append(OutputColumn("__total", "dimension", "string"))
        elif hidden:
            # Drop the hidden sort measures but keep their order.
            out = (
                exp.select(*[_col("q", n) for n in visible])
                .from_(out.subquery("q"))
                .order_by(
                    *[
                        exp.Ordered(
                            this=_col("q", o.this.name), desc=bool(o.args.get("desc")), nulls_first=False
                        )
                        for o in order
                    ]
                )
            )
        return out, extra

    def _pivot(
        self,
        out: exp.Select,
        dims: list[tuple[str, Node, str]],
        measures: list[tuple[str, Node]],
        extra: list[OutputColumn],
    ) -> exp.Select:
        """Tableau's Measure Names / Measure Values: one row per dimension member and measure."""
        if not measures:
            raise CompileError("pivotMeasures needs at least one measure")
        passthrough = [n for n, _, _ in dims] + [c.name for c in extra]
        parts: list[exp.Select] = []
        for i, (name, _) in enumerate(measures):
            parts.append(
                exp.select(
                    *[_col("q", n) for n in passthrough],
                    exp.alias_(_str(name), "Measure Names", quoted=True),
                    exp.alias_(_cast(_col("q", name), "float"), "Measure Values", quoted=True),
                    exp.alias_(_num(i), "__measure_order", quoted=True),
                ).from_(out.subquery("q"))
            )
        union: exp.Query = parts[0]
        for part in parts[1:]:
            union = exp.union(union, part, distinct=False)
        ordering = [exp.Ordered(this=_col(None, n), nulls_first=False) for n, _, _ in dims]
        return (
            exp.select(
                *[_col(None, n) for n in passthrough],
                _col(None, "Measure Names"),
                _col(None, "Measure Values"),
            )
            .from_(union.subquery("p"))
            .order_by(*ordering, exp.Ordered(this=_col(None, "__measure_order")))
        )

    # -- requests ------------------------------------------------------------------------------
    def _dimension(self, spec: Any) -> tuple[str, Node, str]:
        if isinstance(spec, str):
            return spec, self.model.field_node(spec), "dimension"
        if "expr" in spec:
            node = self.model.expand_formula(spec["expr"])
        else:
            node = self.model.field_node(spec["field"])
            fiscal = self.model.fiscal_year_start != 1
            if spec.get("dateTrunc"):
                fn = (
                    "_FISCAL_DATETRUNC"
                    if fiscal and spec["dateTrunc"] in ("year", "quarter")
                    else "DATETRUNC"
                )
                node = Call(fn, (Lit("string", spec["dateTrunc"]), node))
            elif spec.get("datePart"):
                fn = "_FISCAL_DATEPART" if fiscal and spec["datePart"] in ("year", "quarter") else "DATEPART"
                node = Call(fn, (Lit("string", spec["datePart"]), node))
        if any(is_aggregate_call(n) for n in _outside_lods(node)):
            raise CompileError(f"dimension {spec!r} is an aggregate; use it as a measure")
        return spec.get("as") or spec["field"], node, "dimension"

    def _measure(self, spec: Any) -> tuple[str, Node]:
        if isinstance(spec, str):
            return spec, measure_node(self.model, spec)
        if "expr" in spec:
            node = self.model.expand_formula(spec["expr"])
            if not any(is_aggregate_call(n) for n in _outside_lods(node)):
                node = Call("SUM", (node,))
            return spec["as"], node
        return spec.get("as") or spec["field"], measure_node(self.model, spec["field"], spec["agg"])

    def _split_filters(
        self, where: list[Mapping[str, Any]]
    ) -> tuple[
        list[tuple[Node, Mapping[str, Any]]],
        list[tuple[Node, Mapping[str, Any]]],
        list[tuple[Node, Mapping[str, Any]]],
    ]:
        context: list[tuple[Node, Mapping[str, Any]]] = []
        dimension: list[tuple[Node, Mapping[str, Any]]] = []
        measure: list[tuple[Node, Mapping[str, Any]]] = []
        for spec in where:
            if "expr" in spec:
                target = self.model.expand_formula(spec["expr"])
            else:
                target = self.model.field_node(spec["field"])
            is_measure = "field" in spec and self.model.field(spec["field"]).role == "measure"
            aggregate = any(is_aggregate_call(n) for n in _outside_lods(target))
            if spec["kind"] in ("range", "condition") and (
                aggregate or (is_measure and spec["kind"] == "range")
            ):
                if spec["kind"] == "range" and not aggregate:
                    target = measure_node(self.model, spec["field"])
                node = self._predicate(target, spec)
                if spec.get("context"):
                    raise UnsupportedError("aggregate filters cannot be context filters")
                measure.append((node, spec))
            elif spec.get("context"):
                context.append((target, spec))
            else:
                dimension.append((target, spec))
        return context, dimension, measure

    def _applies(self, policy: Any) -> bool:
        exempt = self.session.groups & policy.exempt_groups or self.session.username in policy.exempt_users
        return not exempt

    def _entitlements(self) -> list[Any]:
        return [p.entitlement for p in self.model.rls if p.entitlement is not None and self._applies(p)]

    def _entitlement(self, ent: Any, i: int, scope: _RowScope) -> exp.Expr:
        """``EXISTS (SELECT 1 FROM entitlements e WHERE e.user = :viewer AND e.key = row.key ...)``."""
        alias = f"ent{i}"
        user_col = _col(alias, _validated(ent.user_column, "column"))
        who: exp.Expr
        if ent.match_on == "group":
            groups = sorted(self.session.groups)
            if not groups:
                return exp.false()
            who = exp.In(this=user_col, expressions=[self._bind(g, "string").e for g in groups])
        else:
            value = self.session.username if ent.match_on == "username" else self.session.email
            if not value:
                return exp.false()
            who = exp.EQ(this=user_col, expression=self._bind(value, "string").e)
        keys: list[exp.Expr] = [
            exp.EQ(
                this=_col(alias, _validated(column, "column")),
                expression=self._row(self.model.expand_formula(_field_ref(name)), scope).e,
            )
            for name, column in ent.keys
        ]
        sub = (
            exp.select(exp.Literal.number(1))
            .from_(self._table_ref(ent.table, alias))
            .where(exp.and_(who, *keys))
        )
        return exp.Exists(this=sub)

    def _rls_conditions(self) -> list[Node]:
        out = []
        for policy in self.model.rls:
            if not self._applies(policy):
                continue
            if policy.filter:
                node = self.model.expand_formula(policy.filter)
                if any(isinstance(n, Lod) or is_aggregate_call(n) for n in walk(node)):
                    raise UnsupportedError(f"RLS policy {policy.id!r} must be a row-level condition")
                out.append(node)
        return out

    # -- filters -------------------------------------------------------------------------------
    def _filter_condition(self, target: Node, spec: Mapping[str, Any], scope: _RowScope) -> exp.Expr:
        kind = spec["kind"]
        if kind == "top_n":
            return self._top_n(target, spec, scope)
        if kind == "condition":
            # A condition on a dimension: keep members whose condition holds over context-filtered data.
            cond = self.model.expand_formula(spec["condition"])
            return self._semi_join(target, cond, scope, None)
        return self._row(self._predicate(target, spec), scope).e

    def _predicate(self, target: Node, spec: Mapping[str, Any]) -> Node:
        """A boolean calc AST for categorical, range, relative-date, wildcard and condition filters."""
        kind = spec["kind"]
        include_nulls = spec.get("includeNulls")
        cond: Node
        if kind == "categorical":
            members = list(spec.get("members", []))
            values = [m for m in members if m is not None]
            has_null = len(values) != len(members)
            parts: list[Node] = []
            if values:
                parts.append(InList(target, tuple(_scalar(v) for v in values)))
            if has_null:
                parts.append(Call("ISNULL", (target,)))
            if not parts:
                cond = Lit("boolean", False)
            else:
                cond = parts[0] if len(parts) == 1 else Binary("OR", parts[0], parts[1])
            if spec.get("exclude"):
                # Tableau's exclude keeps NULLs unless NULL is one of the excluded members.
                negated: Node = Unary("NOT", cond)
                cond = negated if has_null else Binary("OR", Call("ISNULL", (target,)), negated)
            return cond
        if kind == "range":
            parts = []
            if spec.get("min") is not None:
                parts.append(Binary(">=", target, _scalar(spec["min"])))
            if spec.get("max") is not None:
                parts.append(Binary("<=", target, _scalar(spec["max"])))
            cond = parts[0] if len(parts) == 1 else Binary("AND", parts[0], parts[1])
            if include_nulls:
                cond = Binary("OR", cond, Call("ISNULL", (target,)))
            return cond
        if kind == "relative_date":
            rel = spec["relative"]
            period = rel["period"]
            anchor: Node = Lit("date", rel["anchor"]) if rel.get("anchor") else Call("TODAY", ())
            trunc_anchor = Call("DATETRUNC", (Lit("string", period), anchor))
            lo = Call(
                "DATEADD", (Lit("string", period), Lit("integer", int(rel.get("from", 0))), trunc_anchor)
            )
            hi = Call("DATEADD", (Lit("string", period), Lit("integer", int(rel.get("to", 0))), trunc_anchor))
            truncated = Call("DATETRUNC", (Lit("string", period), target))
            return Binary("AND", Binary(">=", truncated, lo), Binary("<=", truncated, hi))
        if kind == "wildcard":
            wc = spec["wildcard"]
            value: Node = Lit("string", wc["value"])
            subject = target
            if not wc.get("caseSensitive"):
                subject, value = Call("LOWER", (target,)), Call("LOWER", (value,))
            fn = {"contains": "CONTAINS", "starts_with": "STARTSWITH", "ends_with": "ENDSWITH"}.get(
                wc["match"]
            )
            cond = Binary("=", subject, value) if fn is None else Call(fn, (subject, value))
            return Unary("NOT", cond) if spec.get("exclude") else cond
        if kind == "condition":
            return self.model.expand_formula(spec["condition"])
        raise UnsupportedError(f"filter kind {kind!r}")

    def _top_n(self, target: Node, spec: Mapping[str, Any], scope: _RowScope) -> exp.Expr:
        top = spec["topN"]
        n = top["n"]
        if isinstance(n, dict):
            n = self._param_value(n["param"])
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise CompileError("Top N needs a positive integer")
        by = self.model.expand_formula(top["by"])
        return self._semi_join(target, by, scope, (n, top.get("direction", "top") == "bottom"))

    def _set_membership(self, node: Call, scope: _RowScope) -> exp.Expr:
        """A computed set: members of its field ranked by Top N, or passing a condition."""
        set_id = str(node.args[0].value) if isinstance(node.args[0], Lit) else ""
        spec = self.model.sets.get(set_id.casefold())
        if spec is None:
            raise CompileError(f"unknown set {set_id!r}")
        target = node.args[1]
        if "condition" in spec:
            return self._semi_join(target, self.model.expand_formula(spec["condition"]), scope, None)
        top = spec["top"]
        n = top["n"]
        if isinstance(n, Mapping):
            n = self._param_value(n["param"])
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise CompileError(f"set {spec['name']!r}: Top N needs a positive integer")
        by = self.model.expand_formula(top["by"])
        return self._semi_join(target, by, scope, (n, top.get("direction", "top") == "bottom"))

    def _semi_join(
        self, target: Node, value: Node, scope: _RowScope, top: tuple[int, bool] | None
    ) -> exp.Expr:
        """``target IN (members of target ranked or kept by an aggregate over base)``."""
        if not any(is_aggregate_call(n) for n in _outside_lods(value)):
            value = Call("SUM", (value,))
        agg = self._aggregate("base", (target,), (value,))
        sub = exp.select(_col(None, "k0")).from_(_ident(agg))
        if top is None:
            sub = sub.where(_col(None, "m0"))
        else:
            n, bottom = top
            sub = sub.where(exp.Not(this=exp.Is(this=_col(None, "m0"), expression=exp.Null())))
            sub = sub.order_by(exp.Ordered(this=_col(None, "m0"), desc=not bottom)).limit(n)
            sub = exp.select(_col(None, "k0")).from_(sub.subquery("top_n"))
        return exp.In(this=self._row(target, scope).e, query=exp.Subquery(this=sub))

    # -- sources -------------------------------------------------------------------------------
    def _register_columns(self, node: Node) -> None:
        for n in walk(node):
            if isinstance(n, Field) and self.model.parameter_for(n) is None:
                f = self.model.column_of(n)
                if f.column is None:  # column_of guarantees a column; this keeps mypy informed
                    raise CompileError(f"{n.name!r} is not a column")
                self.columns.setdefault(f.id, f"c{len(self.columns)}_{_validated(f.column.name, 'column')}")
                self.tables_used.add(f.column.table)
            if isinstance(n, Call) and n.name in TABLE_CALCS:
                raise UnsupportedError(
                    f"{n.name} is a table calculation; declare it in the query's tableCalcs"
                )
            if isinstance(n, Call) and n.name == "_SET" and isinstance(n.args[0], Lit):
                spec = self.model.sets.get(str(n.args[0].value).casefold(), {})
                formula = spec.get("condition") or (spec.get("top") or {}).get("by")
                if formula:
                    self._register_columns(self.model.expand_formula(formula))

    def _table_ref(self, table_id: str, alias: str) -> exp.Expr:
        table = self.model.tables[table_id]
        source = table.source
        if "sql" in source:
            sub = sqlglot.parse_one(source["sql"], read=self._read_dialect())
            if not isinstance(sub, exp.Query):
                raise CompileError(f"custom SQL of table {table_id!r} is not a query")
            return sub.subquery(alias)
        conn = self.model.connection
        db = source.get("database") or source.get("schema") or conn.get("schema") or conn.get("database")
        name = _validated(source["table"], "table")
        return exp.Table(
            this=_ident(name),
            db=_ident(_validated(db, "database")) if db else None,
            alias=exp.TableAlias(this=_ident(alias)),
        )

    def _read_dialect(self) -> str:
        return "trino" if self.dialect == "athena" else self.dialect

    def _build_src(self) -> None:
        tables = sorted(self.tables_used) or [next(iter(self.model.tables))]
        root, joins = self._join_tree(tables)
        self.root_table = root
        aliases = {t: f"t{i}" for i, t in enumerate([root, *[j[0] for j in joins]])}
        # Primary keys of joined ("one"-side) tables: measures from those tables are aggregated over
        # their own rows (Tableau's native grain), not over the fanned-out join.
        keys: list[exp.Expr] = []
        for table, _parent, _on in joins:
            pk = self.model.tables[table].primary_key
            self.pk_columns[table] = [f"pk_{table}_{_validated(c, 'column')}" for c in pk]
            keys += [
                exp.alias_(_col(aliases[table], c), alias, quoted=True)
                for c, alias in zip(pk, self.pk_columns[table], strict=True)
            ]
        select = exp.select(
            *(
                [
                    exp.alias_(_col(aliases[f.column.table], f.column.name), alias, quoted=True)
                    for fid, alias in self.columns.items()
                    if (f := self.model.fields[fid]).column is not None
                ]
                + keys
            )
            or [exp.alias_(exp.Literal.number(1), "one", quoted=True)]
        ).from_(self._table_ref(root, aliases[root]))
        for table, parent, on in joins:
            # Relationship keys never match on NULL, as in Tableau (LOD joins, below, do).
            cond = exp.and_(
                *[
                    exp.EQ(
                        this=_col(aliases[parent], _validated(lc, "column")),
                        expression=_col(aliases[table], _validated(rc, "column")),
                    )
                    for lc, rc in on
                ]
            )
            select = select.join(self._table_ref(table, aliases[table]), on=cond, join_type="left")
        self._add_cte("src", select)

    def _join_tree(self, tables: list[str]) -> tuple[str, list[tuple[str, str, tuple[tuple[str, str], ...]]]]:
        """Pick a root from which every needed table is reached through many-to-one joins."""
        if len(tables) == 1:
            return tables[0], []
        edges: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = {}
        for r in self.model.relationships:
            if r.cardinality in ("many_to_one", "one_to_one"):
                edges.setdefault(r.left, []).append((r.right, r.on))
            if r.cardinality in ("one_to_many", "one_to_one"):
                edges.setdefault(r.right, []).append((r.left, tuple((b, a) for a, b in r.on)))
        for root in tables:
            seen = {root}
            order: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
            frontier = [root]
            while frontier:
                current = frontier.pop(0)
                for nxt, on in edges.get(current, []):
                    if nxt not in seen:
                        seen.add(nxt)
                        order.append((nxt, current, on))
                        frontier.append(nxt)
            if set(tables) <= seen:
                needed = self._prune(order, set(tables), root)
                return root, needed
        raise UnsupportedError(
            f"tables {tables} are not linked by many-to-one relationships; "
            "native-grain relationship planning arrives with the full planner (P2.5)"
        )

    @staticmethod
    def _prune(
        order: list[tuple[str, str, tuple[tuple[str, str], ...]]], needed: set[str], root: str
    ) -> list[tuple[str, str, tuple[tuple[str, str], ...]]]:
        parents = {t: p for t, p, _ in order}
        keep = set()
        for t in needed - {root}:
            while t != root and t not in keep:
                keep.add(t)
                t = parents[t]
        return [o for o in order if o[0] in keep]

    def _add_cte(self, name: str, select: exp.Select) -> str:
        self.ctes.append(_Cte(name, select))
        return name

    def _new_name(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    def _scope(self, relation: str, alias: str, source: str, grain: tuple[Node, ...]) -> _RowScope:
        return _RowScope(relation=relation, alias=alias, source=source, grain=grain)

    def _from(self, scope: _RowScope, select: exp.Select) -> exp.Select:
        select = select.from_(
            exp.Table(this=_ident(scope.relation), alias=exp.TableAlias(this=_ident(scope.alias)))
        )
        for join in scope.joins:
            select = select.join(join)
        return select

    def _select_star(self, scope: _RowScope, conditions: list[exp.Expr]) -> exp.Select:
        select = self._from(scope, exp.select(exp.Column(this=exp.Star(), table=_ident(scope.alias))))
        if conditions:
            select = select.where(exp.and_(*conditions))
        return select

    # -- aggregation ---------------------------------------------------------------------------
    def _aggregate(self, source: str, grain: tuple[Node, ...], measures: tuple[Node, ...]) -> str:
        """A CTE with columns k0..kn (grain) and m0..mn (measures), one row per grain member."""
        key = (source, grain, measures)
        if key in self.agg_cache:
            return self.agg_cache[key]
        # INCLUDE/EXCLUDE are relative to the view's dimensions, not to LOD-valued dimensions.
        lod_free = tuple(g for g in grain if not any(isinstance(n, Lod) for n in walk(g)))
        scope = self._scope(source, "s", source, lod_free)
        atoms: dict[Call, str] = {}  # row-relation atoms, computed in the main select
        domain_atoms: dict[Call, str] = {}  # LOD-only atoms, computed over a distinct domain
        posts = [self._post(m, grain, atoms, domain_atoms, scope) for m in measures]

        name = self._new_name("agg")
        if not measures:
            # Dimensions only: distinct members.
            select = self._from(
                scope,
                exp.select(
                    *[exp.alias_(self._row(g, scope).e, f"k{i}", quoted=True) for i, g in enumerate(grain)]
                ),
            )
            select = select.distinct() if grain else select.limit(1)
            self._add_cte(name, select)
            self.agg_cache[key] = name
            return name

        main = exp.select(
            *[exp.alias_(self._row(g, scope).e, f"k{i}", quoted=True) for i, g in enumerate(grain)],
            *[exp.alias_(self._agg_call(c, scope).e, alias, quoted=True) for c, alias in atoms.items()],
        )
        if not grain and not atoms:
            # Only LOD measures and no dimensions: a single anchor row for the cross joins below.
            main = exp.select(exp.alias_(exp.Literal.number(1), "one", quoted=True))
        else:
            main = self._from(scope, main)
        if grain:
            main = main.group_by(*[exp.Literal.number(i + 1) for i in range(len(grain))])
        main_name = self._add_cte(f"{name}_a", main)

        final = exp.select(
            *[exp.alias_(_col("g", f"k{i}"), f"k{i}", quoted=True) for i in range(len(grain))],
            *[exp.alias_(p.e, f"m{i}", quoted=True) for i, p in enumerate(posts)],
        ).from_(exp.Table(this=_ident(main_name), alias=exp.TableAlias(this=_ident("g"))))
        for call, alias in domain_atoms.items():
            sub = (
                self._native_atom(call, source, grain)
                if call in self.native_atoms
                else self._domain_atom(call, source, grain)
            )
            if grain:
                on = exp.and_(
                    *[_null_safe_eq(_col("g", f"k{i}"), _col(alias, f"k{i}")) for i in range(len(grain))]
                )
                final = final.join(
                    exp.Table(this=_ident(sub), alias=exp.TableAlias(this=_ident(alias))),
                    on=on,
                    join_type="left",
                )
            else:
                final = final.join(
                    exp.Table(this=_ident(sub), alias=exp.TableAlias(this=_ident(alias))), join_type="cross"
                )
        self._add_cte(name, final)
        self.agg_cache[key] = name
        return name

    def _post(
        self,
        node: Node,
        grain: tuple[Node, ...],
        atoms: dict[Call, str],
        domain_atoms: dict[Call, str],
        scope: _RowScope,
    ) -> T:
        """Translate an aggregate expression, replacing each aggregate call by a column reference."""

        def leaf(n: Node) -> T | None:
            if isinstance(n, Call) and is_aggregate_call(n):
                type_ = self._agg_type(n, scope)
                if any(isinstance(x, Lod) for x in walk(n)) and not self._reads_columns(n):
                    alias = domain_atoms.setdefault(n, f"d{len(domain_atoms)}")
                    return T(_col(alias, "v"), type_)
                if self._fan_out_table(n) is not None:
                    self.native_atoms.add(n)
                    alias = domain_atoms.setdefault(n, f"d{len(domain_atoms)}")
                    return T(_col(alias, "v"), type_)
                alias = atoms.setdefault(n, f"a{len(atoms)}")
                return T(_col("g", alias), type_)
            if n in grain:
                i = grain.index(n)
                return T(_col("g", f"k{i}"), self._row(n, scope).type)
            if isinstance(n, Field) and self.model.parameter_for(n) is None:
                raise CompileError(f"{to_formula(n)} must be aggregated or be a dimension of the query")
            if isinstance(n, Lod):
                raise CompileError(f"{to_formula(n)} must be aggregated in a measure")
            return None

        return self._translate(node, leaf)

    @staticmethod
    def _reads_columns(call: Call) -> bool:
        return any(isinstance(n, Field) for n in _outside_lods(call) if not isinstance(n, Lod))

    def _agg_type(self, call: Call, scope: _RowScope) -> str:
        if call.name in ("COUNT", "COUNTD"):
            return "integer"
        if call.name in (
            "AVG",
            "MEDIAN",
            "STDEV",
            "STDEVP",
            "VAR",
            "VARP",
            "PERCENTILE",
            "CORR",
            "COVAR",
            "COVARP",
        ):
            return "float"
        return self._row(call.args[0], scope, probe=True).type if call.args else "unknown"

    def _fan_out_table(self, call: Call) -> str | None:
        """The joined ("one"-side) table an aggregate reads only from, if any: its rows repeat."""
        if any(isinstance(x, Lod) for x in walk(call)):
            return None
        tables = set()
        for ref in (n for n in walk(call) if isinstance(n, Field) and self.model.parameter_for(n) is None):
            column = self.model.column_of(ref).column
            if column is not None:
                tables.add(column.table)
        if len(tables) != 1:
            return None
        table = next(iter(tables))
        if table == self.root_table or table not in self.pk_columns:
            return None
        if not self.pk_columns[table]:
            raise UnsupportedError(
                f"measures from table {table!r} repeat across the join; give the table a primaryKey "
                "so they can be aggregated at its own grain"
            )
        return table

    def _native_atom(self, call: Call, source: str, grain: tuple[Node, ...]) -> str:
        """AGG over the distinct rows of the measure's own table within each view-grain member."""
        table = self._fan_out_table(call)
        if table is None:  # pragma: no cover - only called for fan-out atoms
            raise CompileError("not a related-table measure")
        src_scope = self._scope(source, "s", source, grain)
        args = [a for a in call.args if not isinstance(a, Lit)]
        domain = exp.select(
            *[exp.alias_(self._row(g, src_scope).e, f"k{i}", quoted=True) for i, g in enumerate(grain)],
            *[_col("s", alias) for alias in self.pk_columns[table]],
            *[exp.alias_(self._row(a, src_scope).e, f"v{j}", quoted=True) for j, a in enumerate(args)],
        )
        domain = self._from(src_scope, domain).distinct()
        domain_name = self._add_cte(self._new_name("native"), domain)
        scope = self._scope(domain_name, "dm", source, grain)
        scope.keymap = {g: f"k{i}" for i, g in enumerate(grain)}
        scope.keymap.update({a: f"v{j}" for j, a in enumerate(args)})
        value = self._agg_call(call, scope)
        select = self._from(
            scope,
            exp.select(
                *[exp.alias_(_col("dm", f"k{i}"), f"k{i}", quoted=True) for i in range(len(grain))],
                exp.alias_(value.e, "v", quoted=True),
            ),
        )
        if grain:
            select = select.group_by(*[exp.Literal.number(i + 1) for i in range(len(grain))])
        return self._add_cte(self._new_name("nativeagg"), select)

    def _domain_atom(self, call: Call, source: str, grain: tuple[Node, ...]) -> str:
        """AGG(f({LOD}...)) over the distinct (view grain + LOD dimensions) domain."""
        lods = _top_lods(call)
        lod_dims: list[Node] = []
        for lod in lods:
            for d in self._lod_grain(lod, grain):
                if d not in grain and d not in lod_dims:
                    lod_dims.append(d)
        keys = list(grain) + lod_dims
        src_scope = self._scope(source, "s", source, grain)
        domain = self._from(
            src_scope,
            exp.select(
                *[exp.alias_(self._row(k, src_scope).e, f"k{i}", quoted=True) for i, k in enumerate(keys)]
            ),
        )
        domain = domain.distinct() if keys else domain.limit(1)
        domain_name = self._add_cte(
            self._new_name("dom"),
            domain if keys else exp.select(exp.alias_(exp.Literal.number(1), "one", quoted=True)),
        )
        scope = self._scope(domain_name, "dm", source, grain)
        scope.keymap = {k: f"k{i}" for i, k in enumerate(keys)}
        value = self._agg_call(call, scope)
        select = self._from(
            scope,
            exp.select(
                *[exp.alias_(_col("dm", f"k{i}"), f"k{i}", quoted=True) for i in range(len(grain))],
                exp.alias_(value.e, "v", quoted=True),
            ),
        )
        if grain:
            select = select.group_by(*[exp.Literal.number(i + 1) for i in range(len(grain))])
        return self._add_cte(self._new_name("lodagg"), select)

    def _lod_grain(self, lod: Lod, grain: tuple[Node, ...]) -> tuple[Node, ...]:
        if lod.scope in (None, "fixed"):
            return lod.dims
        if lod.scope == "include":
            return grain + tuple(d for d in lod.dims if d not in grain)
        return tuple(g for g in grain if g not in lod.dims)

    def _lod_value(self, lod: Lod, scope: _RowScope) -> T:
        """Join the LOD's aggregate onto ``scope`` by its dimensions and return its value column."""
        lod_grain = self._lod_grain(lod, scope.grain)
        cache_key = (lod, lod_grain)
        source = "base" if lod.scope in (None, "fixed") else scope.source
        body = lod.body
        if not any(is_aggregate_call(n) for n in _outside_lods(body)):
            body = Call("SUM", (body,))  # Tableau requires an aggregate; SUM is the implied default
        agg = self._aggregate(source, lod_grain, (body,))
        alias = scope.lod_cache.get(cache_key)
        if alias is None:
            alias = f"l{len(scope.joins)}_{scope.alias}"
            table = exp.Table(this=_ident(agg), alias=exp.TableAlias(this=_ident(alias)))
            if lod_grain:
                on = exp.and_(
                    *[
                        _null_safe_eq(self._row(d, scope).e, _col(alias, f"k{i}"))
                        for i, d in enumerate(lod_grain)
                    ]
                )
                scope.joins.append(exp.Join(this=table, on=on, side="LEFT"))
            else:
                scope.joins.append(exp.Join(this=table, kind="CROSS"))
            scope.lod_cache[cache_key] = alias
        type_ = self._agg_type(body, scope) if isinstance(body, Call) and is_aggregate_call(body) else "float"
        return T(_col(alias, "m0"), type_)

    def _agg_call(self, call: Call, scope: _RowScope) -> T:
        name = call.name
        if name == "COUNTD":
            arg = self._row(call.args[0], scope)
            return T(exp.Count(this=exp.Distinct(expressions=[arg.e])), "integer")
        if name == "ATTR":
            arg = self._row(call.args[0], scope)
            mn, mx = exp.Min(this=arg.e), exp.Max(this=arg.e.copy())
            return T(_case([(exp.EQ(this=mn, expression=mx), mn.copy())], exp.Null()), arg.type)
        if name == "PERCENTILE":
            if (
                len(call.args) != 2
                or not isinstance(call.args[1], Lit)
                or call.args[1].kind not in ("integer", "float")
            ):
                raise CompileError("PERCENTILE takes an expression and a literal fraction")
            fraction = float(call.args[1].value)  # type: ignore[arg-type]
            if not 0 <= fraction <= 1:
                raise CompileError("PERCENTILE's fraction must be between 0 and 1")
            x = _cast(self._row(call.args[0], scope).e, "float")
            if self.dialect == "duckdb":
                return T(exp.Anonymous(this="QUANTILE_CONT", expressions=[x, _num(fraction)]), "float")
            if self.dialect == "redshift":
                ordered = exp.WithinGroup(
                    this=exp.Anonymous(this="PERCENTILE_CONT", expressions=[_num(fraction)]),
                    expression=exp.Order(expressions=[exp.Ordered(this=x)]),
                )
                return T(ordered, "float")
            # Athena has no exact percentile: approx_percentile (documented approximation).
            return T(exp.Anonymous(this="APPROX_PERCENTILE", expressions=[x, _num(fraction)]), "float")
        if name in ("CORR", "COVAR", "COVARP"):
            if len(call.args) != 2:
                raise CompileError(f"{name} takes two arguments")
            x = _cast(self._row(call.args[0], scope).e, "float")
            y = _cast(self._row(call.args[1], scope).e, "float")
            fn = {"CORR": "CORR", "COVAR": "COVAR_SAMP", "COVARP": "COVAR_POP"}[name]
            return T(exp.Anonymous(this=fn, expressions=[x, y]), "float")
        if len(call.args) != 1:
            raise CompileError(f"{name} takes one argument")
        arg = self._row(call.args[0], scope)
        e = arg.e
        if name == "SUM":
            if arg.type == "boolean":
                raise CompileError("SUM of a boolean")
            return T(exp.Sum(this=e), arg.type if arg.type in _NUMERIC else "float")
        if name == "AVG":
            return T(exp.Avg(this=_cast(e, "float") if arg.type == "integer" else e), "float")
        if name == "MIN":
            return T(exp.Min(this=e), arg.type)
        if name == "MAX":
            return T(exp.Max(this=e), arg.type)
        if name == "COUNT":
            return T(exp.Count(this=e), "integer")
        if name == "MEDIAN":
            return T(
                exp.Median(this=e)
                if self.dialect not in ("athena", "trino")
                else exp.ApproxQuantile(this=e, quantile=exp.Literal.number(0.5)),
                "float",
            )
        if name in ("STDEV", "STDEVP", "VAR", "VARP"):
            cls = {
                "STDEV": exp.Stddev,
                "STDEVP": exp.StddevPop,
                "VAR": exp.Variance,
                "VARP": exp.VariancePop,
            }[name]
            return T(cls(this=_cast(e, "float")), "float")
        raise UnsupportedError(f"aggregate {name}")

    # -- row-level translation -----------------------------------------------------------------
    def _row(self, node: Node, scope: _RowScope, probe: bool = False) -> T:
        def leaf(n: Node) -> T | None:
            if n in scope.keymap:
                return T(_col(scope.alias, scope.keymap[n]), self._type_of(n, scope))
            if isinstance(n, Field):
                param = self.model.parameter_for(n)
                if param is not None:
                    return self._bind(self._param_value(param.id), param.type)
                f = self.model.column_of(n)
                return T(_col(scope.alias, self.columns[f.id]), f.type)
            if isinstance(n, Lod):
                if probe:
                    return T(exp.Null(), "float")
                return self._lod_value(n, scope)
            if isinstance(n, Call) and n.name == "_SET":
                if probe:
                    return T(exp.Null(), "boolean")
                return T(self._set_membership(n, scope), "boolean")
            if is_aggregate_call(n):
                raise CompileError(f"aggregate {to_formula(n)} used where a row-level value is required")
            return None

        return self._translate(node, leaf)

    def _type_of(self, node: Node, scope: _RowScope) -> str:
        probe_scope = _RowScope(scope.relation, scope.alias, scope.source, scope.grain)
        return self._row(node, probe_scope, probe=True).type

    def _param_value(self, name_or_id: str) -> Any:
        param = self.model.parameters.get(name_or_id) or next(
            (p for p in self.model.parameters.values() if p.name.casefold() == name_or_id.casefold()), None
        )
        if param is None:
            raise CompileError(f"unknown parameter {name_or_id!r}")
        for key in (param.id.casefold(), param.name.casefold()):
            if key in self.param_values:
                return self.param_values[key]
        return param.default

    def _bind(self, value: Any, type_: str) -> T:
        if value is None:
            return T(exp.Null(), "null")
        if isinstance(value, bool):
            type_ = "boolean"
        elif isinstance(value, int):
            type_ = "integer" if type_ not in ("float", "decimal") else type_
        elif isinstance(value, float):
            type_ = "float"
        elif isinstance(value, dt.datetime):
            value, type_ = value.isoformat(sep=" "), "datetime"
        elif isinstance(value, dt.date):
            value, type_ = value.isoformat(), "date"
        elif not isinstance(value, str):
            raise CompileError(f"unsupported parameter value {value!r}")
        name = f"tf{self.nonce}p{len(self.binds)}"
        self.binds[name] = value
        placeholder = exp.Placeholder(this=name)
        if type_ in _TEMPORAL or (isinstance(value, str) and type_ in _NUMERIC | {"boolean"}):
            return T(_cast(placeholder, type_), type_)
        return T(placeholder, type_ if type_ != "unknown" else "string")

    def _translate(self, node: Node, leaf: Callable[[Node], T | None]) -> T:
        found = leaf(node)
        if found is not None:
            return found

        def tr(n: Node) -> T:
            return self._translate(n, leaf)

        match node:
            case Lit(kind, value):
                return self._literal(kind, value)
            case Unary("-", operand):
                v = tr(operand)
                if v.type in _TEMPORAL:
                    raise CompileError("cannot negate a date")
                return T(exp.Neg(this=_paren(v.e)), v.type)
            case Unary("NOT", operand):
                return T(exp.Not(this=_paren(tr(operand).e)), "boolean")
            case Binary(op, left, right):
                return self._binary(op, tr(left), tr(right))
            case If(branches, otherwise):
                whens = [(tr(c).e, tr(v)) for c, v in branches]
                other = tr(otherwise) if otherwise is not None else None
                type_ = _unify([v.type for _, v in whens] + ([other.type] if other else []))
                return T(
                    _case(
                        [(c, self._coerce(v, type_)) for c, v in whens],
                        self._coerce(other, type_) if other else None,
                    ),
                    type_,
                )
            case Case(subject, whens, otherwise):
                s = tr(subject)
                pairs = [(tr(w), tr(v)) for w, v in whens]
                other = tr(otherwise) if otherwise is not None else None
                type_ = _unify([v.type for _, v in pairs] + ([other.type] if other else []))
                case_whens = [
                    (exp.EQ(this=s.e.copy(), expression=w.e), self._coerce(v, type_)) for w, v in pairs
                ]
                return T(_case(case_whens, self._coerce(other, type_) if other else None), type_)
            case InList(value, items):
                v = tr(value)
                return T(
                    exp.In(this=v.e, expressions=[self._coerce(tr(i), v.type) for i in items]), "boolean"
                )
            case Call(name, args):
                return self._function(name, args, tr)
        raise UnsupportedError(f"cannot translate {to_formula(node)}")

    def _literal(self, kind: str, value: Any) -> T:
        if kind == "string":
            return T(_str(str(value)), "string")
        if kind == "integer":
            return T(_num(int(value)), "integer")
        if kind == "float":
            return T(_num(float(value)), "float")
        if kind == "boolean":
            return T(exp.Boolean(this=bool(value)), "boolean")
        if kind == "null":
            return T(exp.Null(), "null")
        if kind == "date":
            text = str(value)
            parsed = _parse_date_literal(text)
            if isinstance(parsed, dt.datetime):
                return T(_cast(_str(parsed.isoformat(sep=" ")), "datetime"), "datetime")
            return T(_cast(_str(parsed.isoformat()), "date"), "date")
        raise CompileError(f"literal kind {kind}")

    def _coerce(self, v: T, type_: str) -> exp.Expr:
        if v.type == type_ or type_ in ("unknown", "null") or v.type == "null":
            return v.e
        if type_ == "float" and v.type == "integer":
            return _cast(v.e, "float")
        if type_ == "datetime" and v.type == "date":
            return _cast(v.e, "datetime")
        if type_ == "string":
            return self._to_string(v).e
        return _cast(v.e, type_)

    def _binary(self, op: str, a: T, b: T) -> T:
        if op in ("AND", "OR"):
            cls = exp.And if op == "AND" else exp.Or
            return T(cls(this=_paren(a.e), expression=_paren(b.e)), "boolean")
        if op in ("=", "<>", "<", "<=", ">", ">="):
            type_ = _unify([a.type, b.type])
            left, right = self._coerce(a, type_), self._coerce(b, type_)
            cls_map: dict[str, type[exp.Binary]] = {
                "=": exp.EQ,
                "<>": exp.NEQ,
                "<": exp.LT,
                "<=": exp.LTE,
                ">": exp.GT,
                ">=": exp.GTE,
            }
            return T(cls_map[op](this=_paren(left), expression=_paren(right)), "boolean")
        if op == "+":
            if a.type == "string" or b.type == "string":
                return T(
                    exp.DPipe(
                        this=_paren(self._coerce(a, "string")),
                        expression=_paren(self._coerce(b, "string")),
                        safe=False,
                    ),
                    "string",
                )
            if a.type in _TEMPORAL and b.type in _NUMERIC:
                return self._date_plus_days(a, b)
            if b.type in _TEMPORAL and a.type in _NUMERIC:
                return self._date_plus_days(b, a)
            return T(exp.Add(this=_paren(a.e), expression=_paren(b.e)), _arith_type(a.type, b.type))
        if op == "-":
            if a.type in _TEMPORAL and b.type in _TEMPORAL:
                if a.type == "date" and b.type == "date":
                    return T(exp.DateDiff(this=a.e, expression=b.e, unit=exp.var("DAY")), "integer")
                seconds = exp.DateDiff(
                    this=_cast(a.e, "datetime") if a.type == "date" else a.e,
                    expression=_cast(b.e, "datetime") if b.type == "date" else b.e,
                    unit=exp.var("SECOND"),
                )
                return T(exp.Div(this=_cast(seconds, "float"), expression=_num(86400.0)), "float")
            if a.type in _TEMPORAL and b.type in _NUMERIC:
                return self._date_plus_days(a, T(exp.Neg(this=_paren(b.e)), b.type))
            return T(exp.Sub(this=_paren(a.e), expression=_paren(b.e)), _arith_type(a.type, b.type))
        if op == "*":
            return T(exp.Mul(this=_paren(a.e), expression=_paren(b.e)), _arith_type(a.type, b.type))
        if op == "/":
            return self._divide(a, b)
        if op == "%":
            return T(
                exp.Mod(this=_paren(a.e), expression=_paren(exp.Nullif(this=b.e, expression=_num(0)))),
                _arith_type(a.type, b.type),
            )
        if op == "^":
            base, power = _cast(a.e, "float"), _cast(b.e, "float")
            bad = exp.and_(
                exp.LT(this=base, expression=_num(0)),
                exp.NEQ(this=power, expression=exp.Floor(this=power.copy())),
            )
            return T(_case([(bad, exp.Null())], exp.Pow(this=base.copy(), expression=power.copy())), "float")
        raise UnsupportedError(f"operator {op}")

    def _divide(self, a: T, b: T) -> T:
        # Tableau: "/" is always floating-point division, and dividing by zero gives NULL.
        return T(
            exp.Div(
                this=_cast(a.e, "float"),
                expression=exp.Nullif(this=_cast(b.e, "float"), expression=_num(0)),
                typed=False,
                safe=False,
            ),
            "float",
        )

    def _date_plus_days(self, d: T, n: T) -> T:
        if d.type == "date" and n.type == "integer":
            return T(exp.DateAdd(this=d.e, expression=n.e, unit=exp.var("DAY")), "date")
        seconds = exp.Mul(this=_cast(n.e, "float"), expression=_num(86400))
        return T(
            exp.DateAdd(
                this=_cast(d.e, "datetime"),
                expression=_cast(exp.Round(this=seconds), "integer"),
                unit=exp.var("SECOND"),
            ),
            "datetime",
        )

    def _to_string(self, v: T) -> T:
        if v.type == "string":
            return v
        if v.type == "boolean":
            return T(_case([(v.e, _str("1")), (exp.Not(this=_paren(v.e.copy())), _str("0"))], None), "string")
        return T(_cast(v.e, "string"), "string")

    # -- functions -----------------------------------------------------------------------------
    def _function(self, name: str, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        if name in AGGREGATES and not (name in ("MIN", "MAX") and len(args) == 2):
            raise CompileError(f"aggregate {name} used where a row-level value is required")
        handler = _FUNCTIONS.get(name)
        if handler is None:
            reason = UNSUPPORTED_FUNCTIONS.get(name)
            raise UnsupportedError(f"function {name}: {reason}" if reason else f"function {name}")
        return handler(self, args, tr)

    def _date_part_name(self, node: Node) -> str:
        if not isinstance(node, Lit) or node.kind != "string":
            raise CompileError("date part must be a string literal such as 'month'")
        part = str(node.value).lower().replace("-", "_")  # Tableau spells ISO parts 'iso-week'
        if part not in _DATE_PARTS:
            raise CompileError(f"unknown date part {part!r}")
        return part

    def _week_start_index(self, args: tuple[Node, ...], position: int) -> int:
        if len(args) > position:
            node = args[position]
            if not isinstance(node, Lit) or str(node.value).lower() not in _WEEKDAYS:
                raise CompileError("week start must be a weekday name such as 'monday'")
            return _WEEKDAYS.index(str(node.value).lower())
        return self.week_start

    def _trunc(self, part: str, d: T, week_start: int) -> T:
        ts = d.e if d.type == "datetime" else _cast(d.e, "datetime")
        if part in ("weekday", "dayofyear"):
            part = "day"
        if part == "week":
            # date_trunc('week') starts on Monday; shift so the week starts on week_start.
            shifted = (
                exp.DateAdd(this=ts, expression=_num(-week_start), unit=exp.var("DAY")) if week_start else ts
            )
            trunc = exp.TimestampTrunc(this=shifted, unit=exp.var("WEEK"))
            out = (
                exp.DateAdd(this=trunc, expression=_num(week_start), unit=exp.var("DAY"))
                if week_start
                else trunc
            )
            return T(_cast(out, "datetime"), "datetime")
        if part == "iso_week":
            return T(_cast(self._monday(ts), "datetime"), "datetime")
        if part == "iso_weekday":
            part = "day"
        if part == "iso_year":
            return T(_cast(self._iso_week1(ts), "datetime"), "datetime")
        if part == "iso_quarter":
            weeks = exp.Mul(
                this=exp.Paren(this=exp.Sub(this=self._iso_quarter(ts), expression=_num(1))),
                expression=_num(91),
            )
            start = exp.DateAdd(this=self._iso_week1(ts), expression=weeks, unit=exp.var("DAY"))
            return T(_cast(start, "datetime"), "datetime")
        return T(_cast(exp.TimestampTrunc(this=ts, unit=exp.var(part.upper())), "datetime"), "datetime")

    # -- ISO-8601 calendar: weeks start on Monday; week 1 contains January 4th; quarters are 13 weeks
    #    (the last one takes week 53). Built from portable date arithmetic for every dialect.
    def _monday(self, ts: exp.Expr) -> exp.Expr:
        return exp.TimestampTrunc(this=ts.copy(), unit=exp.var("WEEK"))

    def _iso_year(self, ts: exp.Expr) -> exp.Expr:
        thursday = exp.DateAdd(this=self._monday(ts), expression=_num(3), unit=exp.var("DAY"))
        return self._extract("YEAR", thursday)

    def _iso_week1(self, ts: exp.Expr, years: exp.Expr | None = None) -> exp.Expr:
        """Monday of ISO week 1 of the ISO year of ``ts`` (plus ``years`` years)."""
        thursday = exp.DateAdd(this=self._monday(ts), expression=_num(3), unit=exp.var("DAY"))
        jan1: exp.Expr = exp.TimestampTrunc(this=thursday, unit=exp.var("YEAR"))
        if years is not None:
            jan1 = exp.DateAdd(this=jan1, expression=years, unit=exp.var("YEAR"))
        jan4 = exp.DateAdd(this=jan1, expression=_num(3), unit=exp.var("DAY"))
        return exp.TimestampTrunc(this=jan4, unit=exp.var("WEEK"))

    def _iso_week(self, ts: exp.Expr) -> exp.Expr:
        days = exp.DateDiff(
            this=_cast(self._monday(ts), "date"),
            expression=_cast(self._iso_week1(ts), "date"),
            unit=exp.var("DAY"),
        )
        return exp.Add(this=_cast(exp.Div(this=days, expression=_num(7)), "integer"), expression=_num(1))

    def _iso_quarter(self, ts: exp.Expr) -> exp.Expr:
        q = exp.Add(
            this=_cast(
                exp.Floor(
                    this=exp.Div(
                        this=exp.Paren(this=exp.Sub(this=self._iso_week(ts), expression=_num(1))),
                        expression=_num(13.0),
                    )
                ),
                "integer",
            ),
            expression=_num(1),
        )
        return _case([(exp.GT(this=q, expression=_num(4)), _num(4))], q.copy())

    def _iso_quarter_start(self, ts: exp.Expr, index: exp.Expr, base: exp.Expr) -> exp.Expr:
        """Monday starting ISO quarter ``index`` (= iso_year * 4 + quarter - 1); ``base`` is ts's index."""
        year = exp.Floor(this=exp.Div(this=_cast(index, "float"), expression=_num(4.0)))
        base_year = exp.Floor(this=exp.Div(this=_cast(base, "float"), expression=_num(4.0)))
        quarter = exp.Sub(
            this=index.copy(), expression=exp.Mul(this=_cast(year, "integer"), expression=_num(4))
        )
        week1 = self._iso_week1(ts, _cast(exp.Sub(this=year.copy(), expression=base_year), "integer"))
        days = exp.Mul(this=exp.Paren(this=quarter), expression=_num(91))
        return exp.DateAdd(this=week1, expression=days, unit=exp.var("DAY"))

    def _add_iso_quarters(self, ts: exp.Expr, n: exp.Expr) -> exp.Expr:
        """Tableau keeps the distance from the end of the ISO quarter (a 53-week year's Q4 has 14 weeks)."""
        index = exp.Add(
            this=exp.Mul(this=self._iso_year(ts), expression=_num(4)),
            expression=exp.Sub(this=self._iso_quarter(ts), expression=_num(1)),
        )
        next_start = self._iso_quarter_start(ts, exp.Add(this=index, expression=_num(1)), index.copy())
        to_end = exp.DateDiff(
            this=_cast(next_start, "date"), expression=_cast(ts.copy(), "date"), unit=exp.var("DAY")
        )
        target_next = self._iso_quarter_start(
            ts,
            exp.Add(this=exp.Add(this=index.copy(), expression=_paren(n)), expression=_num(1)),
            index.copy(),
        )
        day = exp.DateAdd(
            this=target_next, expression=exp.Neg(this=exp.Paren(this=to_end)), unit=exp.var("DAY")
        )
        time_of_day = exp.DateDiff(
            this=ts.copy(),
            expression=exp.TimestampTrunc(this=ts.copy(), unit=exp.var("DAY")),
            unit=exp.var("SECOND"),
        )
        return exp.DateAdd(this=_cast(day, "datetime"), expression=time_of_day, unit=exp.var("SECOND"))

    def _extract(self, unit: str, e: exp.Expr) -> exp.Expr:
        return _cast(exp.Extract(this=exp.var(unit), expression=e), "integer")

    def _datepart(self, part: str, d: T, week_start: int) -> T:
        e = d.e
        if part in ("year", "quarter", "month", "day"):
            return T(self._extract(part.upper(), e), "integer")
        if part in ("hour", "minute", "second"):
            ts = e if d.type == "datetime" else _cast(e, "datetime")
            out = self._extract(part.upper(), ts)
            if part == "second":
                out = _cast(exp.Floor(this=exp.Extract(this=exp.var("SECOND"), expression=ts)), "integer")
            return T(out, "integer")
        if part == "dayofyear":
            return T(self._extract("DOY", e), "integer")
        if part in ("weekday", "iso_weekday"):
            iso = self._extract("ISODOW", e) if self.dialect == "duckdb" else self._extract("DAY_OF_WEEK", e)
            if self.dialect == "redshift":
                iso = exp.Add(
                    this=exp.Mod(
                        this=exp.Paren(this=exp.Add(this=self._extract("DOW", e), expression=_num(6))),
                        expression=_num(7),
                    ),
                    expression=_num(1),
                )
            if part == "iso_weekday":
                return T(iso, "integer")
            # 1 = Sunday. Tableau ignores start_of_week for weekday (TDVT datepart.sow.weekday).
            week_start = 6
            shifted = exp.Mod(
                this=exp.Paren(
                    this=exp.Add(this=exp.Sub(this=iso, expression=_num(1)), expression=_num(7 - week_start))
                ),
                expression=_num(7),
            )
            return T(exp.Add(this=exp.Paren(this=shifted), expression=_num(1)), "integer")
        if part == "week":
            # Week 1 contains January 1st; weeks start on week_start.
            year_start = self._trunc("year", d, week_start)
            return T(
                exp.Add(this=self._datediff("week", year_start, d, week_start).e, expression=_num(1)),
                "integer",
            )
        ts = e if d.type == "datetime" else _cast(e, "datetime")
        if part == "iso_week":
            return T(self._iso_week(ts), "integer")
        if part == "iso_year":
            return T(self._iso_year(ts), "integer")
        if part == "iso_quarter":
            return T(self._iso_quarter(ts), "integer")
        raise UnsupportedError(f"DATEPART {part}")

    def _datediff(self, part: str, a: T, b: T, week_start: int) -> T:
        """Tableau counts boundaries crossed: DATEDIFF('month', #2024-01-31#, #2024-02-01#) = 1."""
        if part in ("year", "quarter", "month"):
            ya, yb = self._extract("YEAR", a.e), self._extract("YEAR", b.e)
            if part == "year":
                return T(exp.Sub(this=yb, expression=ya), "integer")
            unit, per = ("QUARTER", 4) if part == "quarter" else ("MONTH", 12)
            ia = exp.Add(this=exp.Mul(this=ya, expression=_num(per)), expression=self._extract(unit, a.e))
            ib = exp.Add(this=exp.Mul(this=yb, expression=_num(per)), expression=self._extract(unit, b.e))
            return T(exp.Sub(this=exp.Paren(this=ib), expression=exp.Paren(this=ia)), "integer")
        if part == "week":
            ta, tb = self._trunc("week", a, week_start), self._trunc("week", b, week_start)
            days = exp.DateDiff(this=_cast(tb.e, "date"), expression=_cast(ta.e, "date"), unit=exp.var("DAY"))
            return T(_cast(exp.Div(this=days, expression=_num(7)), "integer"), "integer")
        if part in ("day", "weekday", "dayofyear"):
            return T(
                exp.DateDiff(this=_cast(b.e, "date"), expression=_cast(a.e, "date"), unit=exp.var("DAY")),
                "integer",
            )
        if part in ("hour", "minute", "second"):
            ta, tb = self._trunc(part, a, week_start), self._trunc(part, b, week_start)
            return T(exp.DateDiff(this=tb.e, expression=ta.e, unit=exp.var(part.upper())), "integer")
        if part.startswith("iso_"):
            sa = a.e if a.type == "datetime" else _cast(a.e, "datetime")
            sb = b.e if b.type == "datetime" else _cast(b.e, "datetime")
            if part == "iso_weekday":
                return self._datediff("day", a, b, week_start)
            if part == "iso_week":
                days = exp.DateDiff(
                    this=_cast(self._monday(sb), "date"),
                    expression=_cast(self._monday(sa), "date"),
                    unit=exp.var("DAY"),
                )
                return T(_cast(exp.Div(this=days, expression=_num(7)), "integer"), "integer")
            ya, yb = self._iso_year(sa), self._iso_year(sb)
            if part == "iso_year":
                return T(exp.Sub(this=yb, expression=ya), "integer")
            ia = exp.Add(this=exp.Mul(this=ya, expression=_num(4)), expression=self._iso_quarter(sa))
            ib = exp.Add(this=exp.Mul(this=yb, expression=_num(4)), expression=self._iso_quarter(sb))
            return T(exp.Sub(this=exp.Paren(this=ib), expression=exp.Paren(this=ia)), "integer")
        raise UnsupportedError(f"DATEDIFF {part}")

    def _dateadd(self, part: str, n: T, d: T) -> T:
        amount = n.e
        if n.type != "integer":
            amount = _cast(exp.Floor(this=amount) if n.type in _NUMERIC else amount, "integer")
        ts0 = d.e if d.type == "datetime" else _cast(d.e, "datetime")
        if part == "iso_year":
            # Same ISO week number and weekday, n ISO years later.
            offset = exp.DateDiff(
                this=_cast(ts0, "date"), expression=_cast(self._iso_week1(ts0), "date"), unit=exp.var("DAY")
            )
            target = exp.DateAdd(this=self._iso_week1(ts0, amount), expression=offset, unit=exp.var("DAY"))
            time_of_day = exp.DateDiff(
                this=ts0.copy(),
                expression=exp.TimestampTrunc(this=ts0.copy(), unit=exp.var("DAY")),
                unit=exp.var("SECOND"),
            )
            return T(
                exp.DateAdd(this=_cast(target, "datetime"), expression=time_of_day, unit=exp.var("SECOND")),
                "datetime",
            )
        if part == "iso_quarter":
            return T(self._add_iso_quarters(ts0, amount), "datetime")
        if part in ("iso_week", "iso_weekday"):
            days = {"iso_week": 7, "iso_weekday": 1}[part]
            step = exp.Mul(this=_paren(amount), expression=_num(days)) if days != 1 else amount
            return T(exp.DateAdd(this=ts0, expression=step, unit=exp.var("DAY")), "datetime")
        unit = part.upper()
        if part == "quarter":
            amount, unit = exp.Mul(this=_paren(amount), expression=_num(3)), "MONTH"
        elif part == "week":
            amount, unit = exp.Mul(this=_paren(amount), expression=_num(7)), "DAY"
        elif part in ("weekday", "dayofyear"):
            unit = "DAY"
        ts = d.e if d.type == "datetime" else _cast(d.e, "datetime")
        return T(exp.DateAdd(this=ts, expression=amount, unit=exp.var(unit)), "datetime")


def _scalar(value: Any) -> Node:
    if value is None:
        return Lit("null", None)
    if isinstance(value, bool):
        return Lit("boolean", value)
    if isinstance(value, int):
        return Lit("integer", value)
    if isinstance(value, float):
        return Lit("float", value)
    if isinstance(value, str):
        return Lit("string", value)
    raise CompileError(f"unsupported filter value {value!r}")


def _parse_date_literal(text: str) -> dt.date | dt.datetime:
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise CompileError(f"unrecognised date literal #{text}#")


def _arith_type(a: str, b: str) -> str:
    if "float" in (a, b) or "decimal" in (a, b):
        return "float"
    if a == "integer" and b == "integer":
        return "integer"
    if "null" in (a, b):
        return a if b == "null" else b
    return "float"


def _unify(types: Sequence[str]) -> str:
    real = [t for t in types if t not in ("null", "unknown")]
    if not real:
        return "null"
    if all(t == real[0] for t in real):
        return real[0]
    if set(real) <= _NUMERIC:
        return "float"
    if set(real) <= _TEMPORAL:
        return "datetime"
    if "string" in real:
        return "string"
    return real[0]


# ---------------------------------------------------------------------------------------------
# Function table
# ---------------------------------------------------------------------------------------------

Handler = Callable[[_Planner, tuple[Node, ...], Callable[[Node], T]], T]
_FUNCTIONS: dict[str, Handler] = {}


def _fn(*names: str, arity: tuple[int, int] | None = None) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        def checked(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
            if arity is not None and not arity[0] <= len(args) <= arity[1]:
                expected = str(arity[0]) if arity[0] == arity[1] else f"{arity[0]}-{arity[1]}"
                raise CompileError(f"{names[0]} takes {expected} arguments, got {len(args)}")
            return handler(p, args, tr)

        for name in names:
            _FUNCTIONS[name] = checked
        return handler

    return register


def _simple(sql_cls: type[exp.Func], type_: str | None = None, cast_float: bool = False) -> Handler:
    def handler(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        v = tr(args[0])
        e = _cast(v.e, "float") if cast_float else v.e
        return T(sql_cls(this=e), type_ or v.type)

    return handler


for _names, _cls, _type, _as_float in (
    (("ABS",), exp.Abs, None, False),
    (("EXP",), exp.Exp, "float", True),
    (("SIGN",), exp.Sign, "integer", False),
    (("UPPER",), exp.Upper, "string", False),
    (("LOWER",), exp.Lower, "string", False),
):
    _fn(*_names, arity=(1, 1))(_simple(_cls, _type, _as_float))


def _guarded(cond: exp.Expr, value: exp.Expr) -> exp.Expr:
    """NULL outside a function's domain: Tableau returns NULL where engines give NaN or infinity."""
    return _case([(cond, value)], exp.Null())


@_fn("SQRT", arity=(1, 1))
def _sqrt(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    x = _cast(tr(args[0]).e, "float")
    return T(_guarded(exp.GTE(this=x, expression=_num(0)), exp.Sqrt(this=x.copy())), "float")


@_fn("LN", arity=(1, 1))
def _ln(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    x = _cast(tr(args[0]).e, "float")
    return T(_guarded(exp.GT(this=x, expression=_num(0)), exp.Ln(this=x.copy())), "float")


@_fn("CEILING", arity=(1, 1))
def _ceiling(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    return T(_cast(exp.Ceil(this=v.e), "integer"), "integer")


@_fn("FLOOR", arity=(1, 1))
def _floor(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    return T(_cast(exp.Floor(this=v.e), "integer"), "integer")


@_fn("ROUND", arity=(1, 2))
def _round(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    # Engines want a 32-bit INTEGER digit count (DuckDB and Trino reject BIGINT here).
    digits = exp.Cast(this=_int_count(tr(args[1])), to=exp.DataType.build("INT")) if len(args) == 2 else None
    out = exp.Round(this=_cast(v.e, "float") if v.type == "integer" else v.e, decimals=digits)
    return T(out, "float" if digits is not None or v.type != "integer" else "integer")


@_fn("SQUARE", arity=(1, 1))
def _square(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    return T(exp.Pow(this=_cast(v.e, "float"), expression=_num(2)), "float")


@_fn("POWER", arity=(2, 2))
def _power(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._binary("^", tr(args[0]), tr(args[1]))


@_fn("LOG", arity=(1, 2))
def _log(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    x = _cast(tr(args[0]).e, "float")
    base = _cast(tr(args[1]).e, "float") if len(args) == 2 else _num(10.0)
    ok = exp.and_(
        exp.GT(this=x, expression=_num(0)),
        exp.GT(this=base, expression=_num(0)),
        exp.NEQ(this=base.copy(), expression=_num(1)),
    )
    return T(_guarded(ok, exp.Div(this=exp.Ln(this=x.copy()), expression=exp.Ln(this=base.copy()))), "float")


@_fn("PI", arity=(0, 0))
def _pi(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(exp.Anonymous(this="PI", expressions=[]), "float")


def _trig(name: str) -> Handler:
    def handler(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        return T(exp.Anonymous(this=name, expressions=[_cast(tr(a).e, "float") for a in args]), "float")

    return handler


def _arc_handler(name: str) -> Handler:
    def handler(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        x = _cast(tr(args[0]).e, "float")
        ok = exp.Between(this=x, low=_num(-1), high=_num(1))
        return T(_guarded(ok, exp.Anonymous(this=name, expressions=[x.copy()])), "float")

    return handler


@_fn("COT", arity=(1, 1))
def _cot(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    x = _cast(tr(args[0]).e, "float")
    tan = exp.Anonymous(this="TAN", expressions=[x])
    return T(exp.Div(this=_num(1.0), expression=exp.Nullif(this=tan, expression=_num(0))), "float")


for _name in ("SIN", "COS", "TAN", "ATAN", "DEGREES", "RADIANS"):
    _fn(_name, arity=(1, 1))(_trig(_name))
_fn("ATAN2", arity=(2, 2))(_trig("ATAN2"))
_fn("ASIN", arity=(1, 1))(_arc_handler("ASIN"))
_fn("ACOS", arity=(1, 1))(_arc_handler("ACOS"))


@_fn("DIV", arity=(2, 2))
def _div(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    a, b = tr(args[0]), tr(args[1])
    q = exp.Div(this=_cast(a.e, "float"), expression=exp.Nullif(this=_cast(b.e, "float"), expression=_num(0)))
    # Trino (and so Athena) has TRUNCATE; TRUNC is not a function there.
    trunc = "TRUNCATE" if p.dialect in ("athena", "trino") else "TRUNC"
    return T(_cast(exp.Anonymous(this=trunc, expressions=[q]), "integer"), "integer")


def _minmax(op: str) -> Handler:
    def handler(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        a, b = tr(args[0]), tr(args[1])
        type_ = _unify([a.type, b.type])
        ea, eb = p._coerce(a, type_), p._coerce(b, type_)
        cmp = exp.LTE(this=ea, expression=eb) if op == "MIN" else exp.GTE(this=ea, expression=eb)
        null = exp.or_(
            exp.Is(this=ea.copy(), expression=exp.Null()), exp.Is(this=eb.copy(), expression=exp.Null())
        )
        return T(_case([(null, exp.Null()), (cmp, ea.copy())], eb.copy()), type_)

    return handler


# Row-level MIN/MAX: NULL if either argument is NULL (unlike some engines' LEAST/GREATEST).
_fn("MIN", arity=(2, 2))(_minmax("MIN"))
_fn("MAX", arity=(2, 2))(_minmax("MAX"))


@_fn("ZN", arity=(1, 1))
def _zn(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    return T(exp.Coalesce(this=v.e, expressions=[_num(0)]), v.type if v.type in _NUMERIC else "integer")


@_fn("IFNULL", arity=(2, 2))
def _ifnull(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    a, b = tr(args[0]), tr(args[1])
    type_ = _unify([a.type, b.type])
    return T(exp.Coalesce(this=p._coerce(a, type_), expressions=[p._coerce(b, type_)]), type_)


@_fn("ISNULL", arity=(1, 1))
def _isnull(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(exp.Is(this=_paren(tr(args[0]).e), expression=exp.Null()), "boolean")


@_fn("IIF", arity=(3, 4))
def _iif(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    test, then, other = tr(args[0]), tr(args[1]), tr(args[2])
    unknown = tr(args[3]) if len(args) == 4 else None
    type_ = _unify([then.type, other.type] + ([unknown.type] if unknown else []))
    whens = [(test.e, p._coerce(then, type_)), (exp.Not(this=_paren(test.e.copy())), p._coerce(other, type_))]
    return T(_case(whens, p._coerce(unknown, type_) if unknown else None), type_)


@_fn("INT", arity=(1, 1))
def _int(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    if v.type == "integer":
        return v
    if v.type == "boolean":
        return T(_case([(v.e, _num(1)), (exp.Not(this=_paren(v.e.copy())), _num(0))], None), "integer")
    if v.type in _TEMPORAL:
        return T(_cast(exp.Floor(this=_days_since_epoch(p, v)), "integer"), "integer")
    if v.type == "string":
        v = T(_cast(v.e, "float"), "float")
    # Truncate toward zero.
    truncated = _case(
        [(exp.LT(this=v.e, expression=_num(0)), exp.Ceil(this=v.e.copy()))], exp.Floor(this=v.e.copy())
    )
    return T(_cast(truncated, "integer"), "integer")


@_fn("FLOAT", arity=(1, 1))
def _float(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    if v.type == "boolean":
        return T(_case([(v.e, _num(1.0)), (exp.Not(this=_paren(v.e.copy())), _num(0.0))], None), "float")
    if v.type in _TEMPORAL:
        return T(_days_since_epoch(p, v), "float")
    return T(_cast(v.e, "float"), "float")


def _days_since_epoch(p: _Planner, v: T) -> exp.Expr:
    """Tableau's date serial: days since 1900-01-01, with the time as a fraction."""
    epoch = _cast(_str("1900-01-01 00:00:00"), "datetime")
    ts = v.e if v.type == "datetime" else _cast(v.e, "datetime")
    seconds = exp.DateDiff(this=ts, expression=epoch, unit=exp.var("SECOND"))
    return exp.Div(this=_cast(seconds, "float"), expression=_num(86400.0))


@_fn("STR", arity=(1, 1))
def _strf(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._to_string(tr(args[0]))


def _try_cast(e: exp.Expr, type_: str) -> exp.Expr:
    return exp.TryCast(this=e, to=_sql_type(type_))


@_fn("ISDATE", arity=(1, 1))
def _isdate(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    return T(exp.Not(this=exp.Is(this=_try_cast(v.e, "date"), expression=exp.Null())), "boolean")


@_fn("DATE", arity=(1, 1))
def _date(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    if v.type in _NUMERIC:
        epoch = _cast(_str("1900-01-01"), "date")
        return T(
            exp.DateAdd(this=epoch, expression=_cast(exp.Floor(this=v.e), "integer"), unit=exp.var("DAY")),
            "date",
        )
    if v.type == "string":
        if p.dialect in ("athena", "trino"):
            # Trino rejects a string with a time of day as a DATE, so fall back through TIMESTAMP.
            via_timestamp = _cast(_try_cast(v.e.copy(), "datetime"), "date")
            return T(exp.Coalesce(this=_try_cast(v.e, "date"), expressions=[via_timestamp]), "date")
        return T(_try_cast(v.e, "date"), "date")
    return T(_cast(v.e, "date"), "date")


@_fn("DATETIME", arity=(1, 1))
def _datetime(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    v = tr(args[0])
    if v.type == "string":
        return T(_try_cast(v.e, "datetime"), "datetime")
    return T(_cast(v.e, "datetime"), "datetime")


@_fn("LEN", arity=(1, 1))
def _len(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(exp.Length(this=p._to_string(tr(args[0])).e), "integer")


def _trim(position: str | None) -> Handler:
    def handler(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        v = tr(args[0])
        return T(exp.Trim(this=v.e, position=position) if position else exp.Trim(this=v.e), "string")

    return handler


_fn("TRIM", arity=(1, 1))(_trim(None))
_fn("LTRIM", arity=(1, 1))(_trim("LEADING"))
_fn("RTRIM", arity=(1, 1))(_trim("TRAILING"))


@_fn("LEFT", arity=(2, 2))
def _left(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, n = tr(args[0]), tr(args[1])
    count = _int_count(n)
    return T(
        _case(
            [(exp.LT(this=count, expression=_num(0)), exp.Null())],
            exp.Substring(this=s.e, start=_num(1), length=count.copy()),
        ),
        "string",
    )


@_fn("RIGHT", arity=(2, 2))
def _right(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, n = tr(args[0]), tr(args[1])
    count = _int_count(n)
    start = exp.Add(this=exp.Sub(this=exp.Length(this=s.e), expression=count), expression=_num(1))
    safe_start = _case([(exp.LT(this=start, expression=_num(1)), _num(1))], start.copy())
    body = _case(
        [(exp.EQ(this=count.copy(), expression=_num(0)), _str(""))],
        exp.Substring(this=s.e.copy(), start=safe_start),
    )
    return T(_case([(exp.LT(this=count.copy(), expression=_num(0)), exp.Null())], body), "string")


def _int_count(n: T) -> exp.Expr:
    if n.type == "integer":
        return n.e
    return _cast(exp.Floor(this=n.e), "integer")


@_fn("MID", arity=(2, 3))
def _mid(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, start = tr(args[0]), tr(args[1])
    st = _int_count(start)
    safe = _case([(exp.LT(this=st, expression=_num(1)), _num(1))], st.copy())
    if len(args) == 3:
        length = _int_count(tr(args[2]))
        sub = exp.Substring(this=s.e, start=safe, length=length)
        return T(_case([(exp.LT(this=length.copy(), expression=_num(0)), exp.Null())], sub), "string")
    return T(exp.Substring(this=s.e, start=safe), "string")


@_fn("CONTAINS", arity=(2, 2))
def _contains(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, sub = tr(args[0]), tr(args[1])
    return T(exp.GT(this=exp.StrPosition(this=s.e, substr=sub.e), expression=_num(0)), "boolean")


@_fn("STARTSWITH", arity=(2, 2))
def _startswith(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, sub = tr(args[0]), tr(args[1])
    head = exp.Substring(this=s.e, start=_num(1), length=exp.Length(this=sub.e))
    return T(exp.EQ(this=head, expression=sub.e.copy()), "boolean")


@_fn("ENDSWITH", arity=(2, 2))
def _endswith(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, sub = tr(args[0]), tr(args[1])
    start = exp.Add(
        this=exp.Sub(this=exp.Length(this=s.e), expression=exp.Length(this=sub.e)), expression=_num(1)
    )
    tail = _case(
        [(exp.LT(this=start, expression=_num(1)), exp.Null())],
        exp.Substring(this=s.e.copy(), start=start.copy()),
    )
    whens = [
        (exp.Is(this=s.e.copy(), expression=exp.Null()), exp.Null()),
        (exp.EQ(this=exp.Length(this=sub.e.copy()), expression=_num(0)), exp.Boolean(this=True)),
    ]
    return T(_case(whens, exp.EQ(this=tail, expression=sub.e.copy())), "boolean")


@_fn("FIND", arity=(2, 3))
def _find(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, sub = tr(args[0]), tr(args[1])
    if len(args) == 3:
        start = _int_count(tr(args[2]))
        safe = _case([(exp.LT(this=start, expression=_num(1)), _num(1))], start.copy())
        rest = exp.Substring(this=s.e, start=safe)
        pos = exp.StrPosition(this=rest, substr=sub.e)
        found = exp.Add(this=exp.Sub(this=pos, expression=_num(1)), expression=safe.copy())
        return T(_case([(exp.EQ(this=pos.copy(), expression=_num(0)), _num(0))], found), "integer")
    return T(exp.StrPosition(this=s.e, substr=sub.e), "integer")


@_fn("REPLACE", arity=(3, 3))
def _replace(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, old, new = (tr(a) for a in args)
    return T(exp.Anonymous(this="REPLACE", expressions=[s.e, old.e, new.e]), "string")


@_fn("SPACE", arity=(1, 1))
def _space(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    n = _int_count(tr(args[0]))
    spaces: exp.Expr
    if p.dialect in ("athena", "trino"):
        # Trino's REPEAT returns an array of its argument, not a string.
        spaces = exp.Anonymous(this="RPAD", expressions=[_str(""), n.copy(), _str(" ")])
    else:
        spaces = exp.Repeat(this=_str(" "), times=n.copy())
    return T(_case([(exp.LT(this=n, expression=_num(0)), exp.Null())], spaces), "string")


@_fn("ASCII", arity=(1, 1))
def _ascii(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s = tr(args[0]).e
    if p.dialect in ("athena", "trino"):
        # Trino has no UNICODE; CODEPOINT takes exactly one character (an empty string gives NULL).
        first = exp.Nullif(this=exp.Substring(this=s, start=_num(1), length=_num(1)), expression=_str(""))
        one = exp.Cast(this=first, to=exp.DataType.build("VARCHAR(1)"))
        return T(exp.Anonymous(this="CODEPOINT", expressions=[one]), "integer")
    return T(exp.Unicode(this=s), "integer")


@_fn("CHAR", arity=(1, 1))
def _char(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(exp.Chr(expressions=[_int_count(tr(args[0]))]), "string")


@_fn("SPLIT", arity=(3, 3))
def _split(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, delim, token = (tr(a) for a in args)
    if not isinstance(args[2], Lit) or not isinstance(args[2].value, int) or args[2].value == 0:
        raise UnsupportedError("SPLIT with a zero or computed token number")
    n = args[2].value
    if n > 0:
        return T(exp.SplitPart(this=s.e, delimiter=delim.e, part_index=token.e), "string")
    # Negative tokens count from the right: split the reversed string by the reversed delimiter.
    part = exp.SplitPart(
        this=exp.Anonymous(this="REVERSE", expressions=[s.e]),
        delimiter=exp.Anonymous(this="REVERSE", expressions=[delim.e]),
        part_index=_num(-n),
    )
    return T(exp.Anonymous(this="REVERSE", expressions=[part]), "string")


# -- regular expressions (Tableau uses ICU syntax; Athena/Trino use Java-style, Redshift POSIX with
#    Perl extensions, DuckDB RE2: the common subset behaves the same) ------------------------------


def _regex_match(p: _Planner, s: exp.Expr, pattern: exp.Expr) -> exp.Expr:
    return exp.RegexpLike(this=s, expression=pattern)


@_fn("REGEXP_MATCH", arity=(2, 2))
def _regexp_match(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, pattern = tr(args[0]), tr(args[1])
    return T(_regex_match(p, s.e, pattern.e), "boolean")


def _regex_group(p: _Planner, s: exp.Expr, pattern: exp.Expr, group: int) -> exp.Expr:
    if p.dialect == "redshift":
        # REGEXP_SUBSTR returns the whole match, or with 'e' only the first group (PCRE via 'p').
        if group > 1:
            raise UnsupportedError("REGEXP_EXTRACT_NTH beyond the first group on Redshift")
        extracted: exp.Expr = exp.Anonymous(
            this="REGEXP_SUBSTR", expressions=[s, pattern, _num(1), _num(1), _str("pe" if group else "p")]
        )
    else:
        extracted = exp.RegexpExtract(this=s, expression=pattern, group=_num(group))
    # Tableau returns NULL when nothing matches (DuckDB would return '').
    return _case([(_regex_match(p, s.copy(), pattern.copy()), extracted)], exp.Null())


@_fn("REGEXP_EXTRACT", arity=(2, 2))
def _regexp_extract(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, pattern = tr(args[0]), tr(args[1])
    return T(_regex_group(p, s.e, pattern.e, 1), "string")


def _capture_groups(pattern: str) -> int:
    """Capturing groups in a regex: "(" not escaped, not in a character class, not "(?"."""
    count, i, in_class = 0, 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(" and not pattern.startswith("(?", i):
            count += 1
        i += 1
    return count


@_fn("REGEXP_EXTRACT_NTH", arity=(3, 3))
def _regexp_extract_nth(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, pattern = tr(args[0]), tr(args[1])
    n = args[2]
    if not isinstance(n, Lit) or not isinstance(n.value, int) or isinstance(n.value, bool):
        raise UnsupportedError("REGEXP_EXTRACT_NTH needs a literal group number")
    groups = _capture_groups(str(args[1].value)) if isinstance(args[1], Lit) else None
    if n.value < 0 or (groups is not None and n.value > groups):
        return T(exp.Null(), "string")  # Tableau: no such group gives NULL; group 0 is the whole match
    return T(_regex_group(p, s.e, pattern.e, n.value), "string")


@_fn("REGEXP_REPLACE", arity=(3, 3))
def _regexp_replace(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, pattern, replacement = (tr(a) for a in args)
    # Tableau replaces every match (sqlglot adds the 'g' flag where an engine needs it).
    return T(exp.RegexpReplace(this=s.e, expression=pattern.e, replacement=replacement.e), "string")


# -- DATEPARSE: ICU date patterns translated per engine ------------------------------------------

#: ICU pattern letter runs → (strptime for DuckDB, MySQL-style for Athena/Trino date_parse, Redshift).
_DATE_TOKENS: dict[str, tuple[str, str, str]] = {
    "yyyy": ("%Y", "%Y", "YYYY"),
    "yyy": ("%Y", "%Y", "YYYY"),
    "y": ("%Y", "%Y", "YYYY"),
    "yy": ("%y", "%y", "YY"),
    "MMMM": ("%B", "%M", "Month"),
    "MMM": ("%b", "%b", "Mon"),
    "MM": ("%m", "%m", "MM"),
    "M": ("%m", "%c", "MM"),
    "dd": ("%d", "%d", "DD"),
    "d": ("%d", "%e", "DD"),
    "HH": ("%H", "%H", "HH24"),
    "H": ("%H", "%k", "HH24"),
    "hh": ("%I", "%h", "HH12"),
    "h": ("%I", "%l", "HH12"),
    "mm": ("%M", "%i", "MI"),
    "m": ("%M", "%i", "MI"),
    "ss": ("%S", "%s", "SS"),
    "s": ("%S", "%s", "SS"),
    "a": ("%p", "%p", "AM"),
    "EEEE": ("%A", "%W", "Day"),
    "eeee": ("%A", "%W", "Day"),
    "EEE": ("%a", "%a", "Dy"),
    "eee": ("%a", "%a", "Dy"),
}


def translate_date_pattern(icu: str, dialect: Dialect) -> str:
    """Translate an ICU date pattern (Tableau's DATEPARSE) into the engine's format string."""
    column = {"duckdb": 0, "athena": 1, "trino": 1, "redshift": 2}[dialect]
    out: list[str] = []
    i = 0
    while i < len(icu):
        c = icu[i]
        if c == "'":  # quoted literal text
            end = icu.find("'", i + 1)
            end = len(icu) if end < 0 else end
            out.append(icu[i + 1 : end] or "'")
            i = end + 1
            continue
        if c.isalpha():
            j = i
            while j < len(icu) and icu[j] == c:
                j += 1
            run = icu[i:j]
            token = _DATE_TOKENS.get(run) or _DATE_TOKENS.get(run[:4])
            if token is None:
                raise UnsupportedError(f"DATEPARSE pattern letters {run!r}")
            out.append(token[column])
            i = j
            continue
        out.append("%%" if c == "%" and column < 2 else c)
        i += 1
    return "".join(out)


@_fn("DATEPARSE", arity=(2, 2))
def _dateparse(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    if not isinstance(args[0], Lit) or args[0].kind != "string":
        raise UnsupportedError("DATEPARSE needs a literal format")
    fmt = _str(translate_date_pattern(str(args[0].value), p.dialect))
    s = p._to_string(tr(args[1])).e
    if p.dialect == "duckdb":
        parsed: exp.Expr = exp.Anonymous(this="TRY_STRPTIME", expressions=[s, fmt])
    elif p.dialect == "redshift":
        parsed = exp.Anonymous(this="TO_TIMESTAMP", expressions=[s, fmt])
    else:  # Athena / Trino: date_parse raises on bad input, TRY turns that into NULL like Tableau
        parsed = exp.Anonymous(
            this="TRY", expressions=[exp.Anonymous(this="DATE_PARSE", expressions=[s, fmt])]
        )
    return T(_cast(parsed, "datetime"), "datetime")


# -- fiscal years: date pills on shelves follow the data source's fiscal year start (Tableau names a
#    fiscal year after the calendar year it ends in). Formula functions stay calendar-based.


def _fiscal_shift(p: _Planner) -> int:
    return (13 - p.model.fiscal_year_start) % 12


@_fn("_FISCAL_DATEPART", arity=(2, 2))
def _fiscal_datepart(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    part = p._date_part_name(args[0])
    d = tr(args[1])
    shifted = p._dateadd("month", T(_num(_fiscal_shift(p)), "integer"), d)
    return p._datepart(part, shifted, p.week_start)


@_fn("_FISCAL_DATETRUNC", arity=(2, 2))
def _fiscal_datetrunc(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    part = p._date_part_name(args[0])
    d = tr(args[1])
    shift = _fiscal_shift(p)
    truncated = p._trunc(part, p._dateadd("month", T(_num(shift), "integer"), d), p.week_start)
    return p._dateadd("month", T(_num(-shift), "integer"), truncated)


# -- more string and date functions ------------------------------------------------------------


@_fn("FINDNTH", arity=(3, 3))
def _findnth(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s, sub = tr(args[0]), tr(args[1])
    n = args[2]
    if not isinstance(n, Lit) or not isinstance(n.value, int) or n.value < 1:
        raise UnsupportedError("FINDNTH needs a literal occurrence number of at least 1")
    # Position of the nth occurrence: length of the first n pieces when splitting on the substring.
    pieces = [
        exp.SplitPart(this=s.e.copy(), delimiter=sub.e.copy(), part_index=_num(i + 1)) for i in range(n.value)
    ]
    lengths: exp.Expr = _num(0)
    for piece in pieces:
        lengths = exp.Add(this=lengths, expression=exp.Length(this=piece))
    position = exp.Add(
        this=exp.Add(
            this=lengths, expression=exp.Mul(this=exp.Length(this=sub.e.copy()), expression=_num(n.value - 1))
        ),
        expression=_num(1),
    )
    found = exp.GTE(
        this=exp.Sub(
            this=exp.Length(this=s.e.copy()),
            expression=exp.Length(
                this=exp.Anonymous(this="REPLACE", expressions=[s.e.copy(), sub.e.copy(), _str("")])
            ),
        ),
        expression=exp.Mul(this=exp.Length(this=sub.e.copy()), expression=_num(n.value)),
    )
    empty = exp.EQ(this=exp.Length(this=sub.e.copy()), expression=_num(0))
    return T(_case([(empty, _num(1)), (found, position)], _num(0)), "integer")


@_fn("PROPER", arity=(1, 1))
def _proper(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    s = tr(args[0]).e
    if p.dialect == "duckdb":
        # DuckDB has no INITCAP: upper-case each word's first letter with a regex.
        words = exp.Anonymous(this="REGEXP_SPLIT_TO_ARRAY", expressions=[exp.Lower(this=s), _str(" ")])
        lam = exp.Lambda(
            this=exp.DPipe(
                this=exp.Upper(this=exp.Substring(this=exp.column("w"), start=_num(1), length=_num(1))),
                expression=exp.Substring(this=exp.column("w"), start=_num(2)),
                safe=False,
            ),
            expressions=[exp.to_identifier("w")],
        )
        return T(
            exp.Anonymous(
                this="ARRAY_TO_STRING",
                expressions=[exp.Anonymous(this="LIST_TRANSFORM", expressions=[words, lam]), _str(" ")],
            ),
            "string",
        )
    return T(exp.Initcap(this=s), "string")


@_fn("MAKEDATETIME", arity=(2, 2))
def _makedatetime(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    day, clock = tr(args[0]), tr(args[1])
    midnight = _cast(_cast(day.e, "date"), "datetime")
    clock_ts = clock.e if clock.type == "datetime" else _cast(clock.e, "datetime")
    seconds = exp.DateDiff(
        this=clock_ts,
        expression=exp.TimestampTrunc(this=clock_ts.copy(), unit=exp.var("DAY")),
        unit=exp.var("SECOND"),
    )
    return T(exp.DateAdd(this=midnight, expression=seconds, unit=exp.var("SECOND")), "datetime")


@_fn("MAKETIME", arity=(3, 3))
def _maketime(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    # Tableau returns a time on its day zero (1899-12-30).
    h, m, sec = (tr(a) for a in args)
    total = exp.Add(
        this=exp.Add(
            this=exp.Mul(this=_paren(h.e), expression=_num(3600)),
            expression=exp.Mul(this=_paren(m.e), expression=_num(60)),
        ),
        expression=_paren(sec.e),
    )
    base = _cast(_str("1899-12-30 00:00:00"), "datetime")
    return T(exp.DateAdd(this=base, expression=_cast(total, "integer"), unit=exp.var("SECOND")), "datetime")


@_fn("USERATTRIBUTEINCLUDES", arity=(2, 2))
def _userattributeincludes(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    name = _literal_string(args[0], "USERATTRIBUTEINCLUDES")
    raw = p.session.attributes.get(name)
    values = [] if raw is None else [v.strip() for v in str(raw).split(",")]
    expected = tr(args[1])
    if not values:
        return T(exp.Boolean(this=False), "boolean")
    return T(exp.In(this=expected.e, expressions=[p._bind(v, "string").e for v in values]), "boolean")


#: Tableau functions TabFree deliberately does not translate, with the reason shown to authors.
UNSUPPORTED_FUNCTIONS: dict[str, str] = {
    **{
        f"RAWSQL_{t}": "pass-through SQL needs a dialect review; rewrite it as a calculation"
        for t in ("BOOL", "DATE", "DATETIME", "INT", "REAL", "SPATIAL", "STR")
    },
    **{
        f"RAWSQLAGG_{t}": "pass-through SQL needs a dialect review; rewrite it as a calculation"
        for t in ("BOOL", "DATE", "DATETIME", "INT", "REAL", "STR")
    },
    **{
        f"SCRIPT_{t}": "analytics extensions (R/Python) are not supported"
        for t in ("BOOL", "INT", "REAL", "STR")
    },
    **{
        f"MODEL_{t}": "predictive modelling functions are not supported yet"
        for t in (
            "PERCENTILE",
            "QUANTILE",
            "EXTENSION_BOOL",
            "EXTENSION_INT",
            "EXTENSION_REAL",
            "EXTENSION_STR",
        )
    },
    **dict.fromkeys(
        (
            "MAKEPOINT",
            "MAKELINE",
            "DISTANCE",
            "BUFFER",
            "AREA",
            "INTERSECTS",
            "SHAPETYPE",
            "OUTLINE",
            "DIFFERENCE",
            "INTERSECTION",
            "SYMDIFFERENCE",
            "VALIDATE",
            "LENGTH",
            "COLLECT",
            "HEXBINX",
            "HEXBINY",
        ),
        "spatial functions arrive with maps (Phase 4)",
    ),
}


@_fn("DATETRUNC", arity=(2, 3))
def _datetrunc(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._trunc(p._date_part_name(args[0]), tr(args[1]), p._week_start_index(args, 2))


@_fn("DATEPART", arity=(2, 3))
def _datepart(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._datepart(p._date_part_name(args[0]), tr(args[1]), p._week_start_index(args, 2))


@_fn("DATEDIFF", arity=(3, 4))
def _datediff(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._datediff(p._date_part_name(args[0]), tr(args[1]), tr(args[2]), p._week_start_index(args, 3))


@_fn("DATEADD", arity=(3, 3))
def _dateadd(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._dateadd(p._date_part_name(args[0]), tr(args[1]), tr(args[2]))


@_fn("DATENAME", arity=(2, 3))
def _datename(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    part = p._date_part_name(args[0])
    d = tr(args[1])
    number = p._datepart(part, d, p._week_start_index(args, 2))
    if part == "month":
        names = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        return T(
            _case(
                [
                    (exp.EQ(this=number.e.copy(), expression=_num(i + 1)), _str(n))
                    for i, n in enumerate(names)
                ],
                None,
            ),
            "string",
        )
    if part == "weekday":
        iso = p._datepart("iso_weekday", d, 0)
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return T(
            _case(
                [(exp.EQ(this=iso.e.copy(), expression=_num(i + 1)), _str(n)) for i, n in enumerate(names)],
                None,
            ),
            "string",
        )
    if part == "iso_week":
        # ISO weeks are written with two digits ("2015-W01").
        text = p._to_string(number).e
        padded = _case(
            [
                (
                    exp.LT(this=number.e.copy(), expression=_num(10)),
                    exp.DPipe(this=_str("0"), expression=text, safe=False),
                )
            ],
            text.copy(),
        )
        return T(padded, "string")
    return p._to_string(number)


def _shortcut(part: str) -> Handler:
    def handler(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
        return p._datepart(part, tr(args[0]), p.week_start)

    return handler


for _part in (
    "year",
    "quarter",
    "month",
    "day",
    "week",
    "iso_year",
    "iso_quarter",
    "iso_week",
    "iso_weekday",
):
    _fn(_part.upper().replace("_", ""), arity=(1, 1))(_shortcut(_part))


@_fn("TODAY", arity=(0, 0))
def _today(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    if p.session.today is not None:
        return p._bind(p.session.today, "date")
    return T(exp.CurrentDate(), "date")


@_fn("NOW", arity=(0, 0))
def _now(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(_cast(exp.CurrentTimestamp(), "datetime"), "datetime")


@_fn("MAKEDATE", arity=(3, 3))
def _makedate(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    y, m, d = (_int_count(tr(a)) for a in args)
    first = exp.Anonymous(
        this="MAKE_DATE" if p.dialect == "duckdb" else "DATE_FROM_PARTS", expressions=[y, m, d]
    )
    if p.dialect in ("athena", "trino"):
        text = exp.Anonymous(
            this="FORMAT", expressions=[_str("%04d-%02d-%02d"), y.copy(), m.copy(), d.copy()]
        )
        return T(_cast(text, "date"), "date")
    return T(first, "date")


# -- viewer identity: always bind parameters, never the engine's current_user -----------------


@_fn("USERNAME", arity=(0, 0))
def _username(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._bind(p.session.username, "string")


@_fn("FULLNAME", arity=(0, 0))
def _fullname(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._bind(p.session.fullname, "string")


@_fn("USERDOMAIN", arity=(0, 0))
def _userdomain(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return p._bind(p.session.domain, "string")


def _literal_string(node: Node, fn: str) -> str:
    if not isinstance(node, Lit) or node.kind != "string":
        raise CompileError(f"{fn} needs a string literal")
    return str(node.value)


@_fn("ISMEMBEROF", arity=(1, 1))
def _ismemberof(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    group = _literal_string(args[0], "ISMEMBEROF")
    return p._bind(group in p.session.groups, "boolean")


@_fn("ISUSERNAME", arity=(1, 1))
def _isusername(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(exp.EQ(this=p._bind(p.session.username, "string").e, expression=tr(args[0]).e), "boolean")


@_fn("ISFULLNAME", arity=(1, 1))
def _isfullname(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    return T(exp.EQ(this=p._bind(p.session.fullname, "string").e, expression=tr(args[0]).e), "boolean")


@_fn("USERATTRIBUTE", arity=(1, 1))
def _userattribute(p: _Planner, args: tuple[Node, ...], tr: Callable[[Node], T]) -> T:
    name = _literal_string(args[0], "USERATTRIBUTE")
    return p._bind(p.session.attributes.get(name), "string")


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------


def _render(
    planner: _Planner,
    tree: exp.Expr,
    dims: list[tuple[str, Node, str]],
    measures: list[tuple[str, Node]],
) -> CompiledQuery:
    dialect = planner.dialect
    sql = tree.sql(dialect=dialect)
    pattern = re.compile(r"[:$@]?(tf" + planner.nonce + r"p\d+)\b")
    order: list[str] = []

    def replace(match: re.Match[str]) -> str:
        order.append(match.group(1))
        return f":{match.group(1)}" if dialect == "redshift" else "?"

    sql = pattern.sub(replace, sql)
    final_scope = _RowScope("filtered", "s", "filtered", tuple(n for _, n, _ in dims))
    columns = [OutputColumn(name, "dimension", planner._type_of(node, final_scope)) for name, node, _ in dims]
    for name, node in measures:
        try:
            type_ = planner._post(node, final_scope.grain, {}, {}, final_scope).type
        except CompileError:
            type_ = "unknown"
        columns.append(OutputColumn(name, "measure", type_))
    positional = tuple(planner.binds[n] for n in order) if dialect != "redshift" else ()
    named = dict(planner.binds) if dialect == "redshift" else {}
    return CompiledQuery(sql, dialect, positional, named, tuple(columns))
