"""Read-only view of a ``tabfree/model@0`` semantic model, with calculated fields expanded to ASTs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tabfree_compiler.calc import Call, Field, Lit, Lod, Node, children, classify, parse

DataType = str  # string integer float decimal boolean date datetime geography


class CompileError(ValueError):
    """The query or model cannot be compiled (unknown field, bad reference, wrong context...)."""


class UnsupportedError(CompileError):
    """Valid Tableau semantics that this compiler does not implement yet."""


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    type: DataType


@dataclass(frozen=True)
class ModelField:
    id: str
    name: str
    role: str  # dimension | measure
    type: DataType
    column: Column | None = None
    formula: str | None = None
    default_aggregation: str | None = None


@dataclass(frozen=True)
class Parameter:
    id: str
    name: str
    type: DataType
    default: Any


@dataclass(frozen=True)
class Table:
    id: str
    source: Mapping[str, Any]
    columns: Mapping[str, Column]
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True)
class Relationship:
    left: str
    right: str
    on: tuple[tuple[str, str], ...]
    cardinality: str


@dataclass(frozen=True)
class Entitlement:
    """A row is visible when the viewer has a matching row in an entitlement table (P3.4)."""

    table: str  # a model table id (not joined to the others)
    user_column: str
    match_on: str  # username | email | group
    keys: tuple[tuple[str, str], ...]  # (model field name, entitlement table column)


@dataclass(frozen=True)
class RlsPolicy:
    id: str
    filter: str | None
    exempt_groups: frozenset[str]
    exempt_users: frozenset[str]
    entitlement: Entitlement | None = None


@dataclass
class SemanticModel:
    id: str
    connection: Mapping[str, Any]
    week_start: str
    fiscal_year_start: int
    tables: dict[str, Table]
    relationships: list[Relationship]
    fields: dict[str, ModelField]  # by id
    parameters: dict[str, Parameter]  # by id
    data_source_filters: list[str]
    rls: list[RlsPolicy]
    groups: dict[str, Mapping[str, Any]] = field(default_factory=dict)  # by casefolded name and id
    sets: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    bins: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    _by_name: dict[str, ModelField] = field(default_factory=dict)
    _params_by_name: dict[str, Parameter] = field(default_factory=dict)
    _expanded: dict[str, Node] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> SemanticModel:
        tables: dict[str, Table] = {}
        for t in doc.get("tables", []):
            cols = {c["name"]: Column(t["id"], c["name"], c["type"]) for c in t.get("columns", [])}
            tables[t["id"]] = Table(t["id"], t["source"], cols, tuple(t.get("primaryKey") or ()))
        rels = [
            Relationship(
                r["left"],
                r["right"],
                tuple((o["left"], o["right"]) for o in r["on"] if o.get("operator", "=") == "="),
                r.get("cardinality", "many_to_one"),
            )
            for r in doc.get("relationships", [])
        ]
        fields: dict[str, ModelField] = {}
        for f in doc.get("fields", []):
            column = None
            if "column" in f:
                table = tables.get(f["table"])
                if table is None:
                    raise CompileError(f"field {f['name']!r} refers to unknown table {f['table']!r}")
                column = table.columns.get(f["column"]) or Column(f["table"], f["column"], f["type"])
            fields[f["id"]] = ModelField(
                f["id"],
                f["name"],
                f["role"],
                f["type"],
                column,
                f.get("formula"),
                f.get("defaultAggregation"),
            )
        params = {
            p["id"]: Parameter(p["id"], p["name"], p["type"], p.get("default"))
            for p in doc.get("parameters", [])
        }
        rls = []
        for p in doc.get("rls", []):
            ent = p.get("entitlement")
            entitlement = None
            if ent:
                if ent["table"] not in tables:
                    raise CompileError(f"RLS policy {p['id']!r}: unknown entitlement table {ent['table']!r}")
                entitlement = Entitlement(
                    ent["table"],
                    ent["userColumn"],
                    ent.get("matchOn", "username"),
                    tuple((k["field"], k["column"]) for k in ent["keys"]),
                )
            rls.append(
                RlsPolicy(
                    p["id"],
                    p.get("filter"),
                    frozenset((p.get("exempt") or {}).get("groups", [])),
                    frozenset((p.get("exempt") or {}).get("users", [])),
                    entitlement,
                )
            )
        model = cls(
            id=doc["id"],
            connection=doc.get("connection", {}),
            week_start=(doc.get("settings") or {}).get("weekStart", "sunday"),
            fiscal_year_start=int((doc.get("settings") or {}).get("fiscalYearStartMonth", 1)),
            tables=tables,
            relationships=rels,
            fields=fields,
            parameters=params,
            data_source_filters=[f["formula"] for f in doc.get("dataSourceFilters", [])],
            rls=rls,
        )
        for kind, store in (("groups", model.groups), ("sets", model.sets), ("bins", model.bins)):
            for item in doc.get(kind, []):
                store.setdefault(str(item["name"]).casefold(), item)
                store.setdefault(str(item["id"]).casefold(), item)
        for f in fields.values():
            model._by_name.setdefault(f.name.casefold(), f)
            model._by_name.setdefault(f.id.casefold(), f)
        for p in params.values():
            model._params_by_name.setdefault(p.name.casefold(), p)
            model._params_by_name.setdefault(p.id.casefold(), p)
        return model

    # -- lookups -------------------------------------------------------------------------------
    def field(self, name: str) -> ModelField:
        found = self._by_name.get(name.casefold())
        if found is None:
            raise CompileError(f"unknown field {name!r}")
        return found

    def parameter_for(self, ref: Field) -> Parameter | None:
        """``[Parameters].[Top N]`` (or a bare ``[Top N]`` that names no field) is a parameter."""
        if len(ref.parts) == 2 and ref.parts[0].casefold() == "parameters":
            param = self._params_by_name.get(ref.parts[1].casefold())
            if param is None:
                raise CompileError(f"unknown parameter {ref.parts[1]!r}")
            return param
        if len(ref.parts) == 1 and ref.name.casefold() not in self._by_name:
            return self._params_by_name.get(ref.name.casefold())
        return None

    # -- expansion -----------------------------------------------------------------------------
    def expand(self, node: Node) -> Node:
        """Inline calculated fields, leaving only column fields, parameters and literals as leaves."""
        return self._expand(node, ())

    def expand_formula(self, formula: str) -> Node:
        return self.expand(parse(formula))

    def field_node(self, name: str) -> Node:
        return self.expand(Field((name,)))

    def _expand(self, node: Node, stack: tuple[str, ...]) -> Node:
        if isinstance(node, Field):
            if self.parameter_for(node) is not None:
                return node
            if len(node.parts) != 1:
                raise UnsupportedError(f"qualified field reference {'.'.join(node.parts)!r}")
            key = node.name.casefold()
            if key not in self._by_name:
                derived = self._derived(key, stack)
                if derived is not None:
                    return derived
            f = self.field(node.name)
            if f.formula is None:
                return Field((f.id,))
            if f.id in stack:
                raise CompileError(f"calculated field {f.name!r} refers to itself")
            cached = self._expanded.get(f.id)
            if cached is None:
                cached = self._expand(parse(f.formula), (*stack, f.id))
                self._expanded[f.id] = cached
            return cached
        return _rebuild(node, [self._expand(c, stack) for c in children(node)])

    def _derived(self, key: str, stack: tuple[str, ...]) -> Node | None:
        """Groups, bins and sets as expressions over their source field."""
        from tabfree_compiler.calc import Binary, Case, InList

        if key in self.groups:
            g = self.groups[key]
            base = self._expand(Field((str(g["field"]),)), stack)
            whens = tuple(
                (_literal(member), Lit("string", str(label)))
                for label, members in g["groups"].items()
                for member in members
            )
            # Ungrouped members keep their own name unless the group has an "Other" bucket.
            other: Node = Lit("string", str(g["otherLabel"])) if g.get("otherLabel") else base
            return Case(base, whens, other)
        if key in self.bins:
            b = self.bins[key]
            base = self._expand(Field((str(b["field"]),)), stack)
            size = b["size"]
            size_node: Node = (
                Field(("Parameters", self.parameters[size["param"]].name))
                if isinstance(size, Mapping)
                else _literal(size)
            )
            return Binary("*", Call("FLOOR", (Binary("/", base, size_node),)), size_node)
        if key in self.sets:
            st = self.sets[key]
            base = self._expand(Field((str(st["field"]),)), stack)
            if "members" in st:
                return InList(base, tuple(_literal(m) for m in st["members"]))
            # Computed sets (Top N or a condition) are semi-joins, planned by the compiler.
            return Call("_SET", (Lit("string", str(st["id"])), base))
        return None

    def column_of(self, ref: Field) -> ModelField:
        """The column field behind an expanded leaf ``Field`` (always a field id)."""
        f = self.fields.get(ref.name) or self.field(ref.name)
        if f.column is None:
            raise CompileError(f"{ref.name!r} is not a column")
        return f


def _literal(value: Any) -> Node:
    if value is None:
        return Lit("null", None)
    if isinstance(value, bool):
        return Lit("boolean", value)
    if isinstance(value, int):
        return Lit("integer", value)
    if isinstance(value, float):
        return Lit("float", value)
    return Lit("string", str(value))


def _rebuild(node: Node, kids: Sequence[Node]) -> Node:
    """Rebuild ``node`` with new children in the order :func:`children` returns them."""
    from tabfree_compiler.calc import Binary, Case, If, InList, Unary

    it = iter(kids)
    match node:
        case Unary(op, _):
            return Unary(op, next(it))
        case Binary(op, _, _):
            return Binary(op, next(it), next(it))
        case Call(name, args):
            return Call(name, tuple(next(it) for _ in args))
        case If(branches, otherwise):
            new_branches = tuple((next(it), next(it)) for _ in branches)
            return If(new_branches, next(it) if otherwise is not None else None)
        case Case(_, whens, otherwise):
            subject = next(it)
            new_whens = tuple((next(it), next(it)) for _ in whens)
            return Case(subject, new_whens, next(it) if otherwise is not None else None)
        case InList(_, items):
            value = next(it)
            return InList(value, tuple(next(it) for _ in items))
        case Lod(scope, dims, _):
            new_dims = tuple(next(it) for _ in dims)
            return Lod(scope, new_dims, next(it))
        case _:
            return node


def measure_node(model: SemanticModel, name: str, agg: str | None = None) -> Node:
    """The aggregate expression a measure stands for in a query."""
    f = model.field(name)
    expanded = model.field_node(name)
    if agg is not None:
        return Call(agg.upper(), (expanded,))
    if f.formula is not None and classify(expanded) == "aggregate":
        return expanded
    default = f.default_aggregation or ("sum" if f.type in ("integer", "float", "decimal") else "count")
    return Call(default.upper(), (expanded,))


__all__ = [
    "Column",
    "CompileError",
    "Lit",
    "ModelField",
    "Parameter",
    "RlsPolicy",
    "SemanticModel",
    "UnsupportedError",
    "measure_node",
]


# ---------------------------------------------------------------------------------------------
# Calculation dependency graph (P2.2)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CalcInfo:
    """One calculated field: what it reads, where it is evaluated, and whether it is valid."""

    id: str
    name: str
    kind: str  # row | aggregate | lod | table_calc | invalid
    depends_on: tuple[str, ...]  # field ids (columns and calcs) referenced directly
    parameters: tuple[str, ...]
    error: str | None = None


def analyze_calcs(model: SemanticModel) -> list[CalcInfo]:
    """Direct dependencies and the evaluation kind of every calculated field, in dependency order.

    The kind is transitive: a row-level formula that references an aggregate calc is an aggregate.
    Parse errors, unknown references and cycles are reported per field instead of raised.
    """
    from tabfree_compiler.calc import CalcSyntaxError, classify, fields_in

    direct: dict[str, tuple[str, ...]] = {}
    params: dict[str, tuple[str, ...]] = {}
    errors: dict[str, str] = {}
    trees: dict[str, Node] = {}
    for f in model.fields.values():
        if f.formula is None:
            continue
        try:
            tree = parse(f.formula)
        except CalcSyntaxError as exc:
            errors[f.id] = f"syntax: {exc}"
            direct[f.id], params[f.id] = (), ()
            continue
        trees[f.id] = tree
        deps: list[str] = []
        ps: list[str] = []
        for ref in fields_in(tree):
            param = model.parameter_for(ref)
            if param is not None:
                ps.append(param.id)
                continue
            try:
                deps.append(model.field(ref.name).id)
            except CompileError:
                key = ref.name.casefold()
                if key not in model.groups and key not in model.sets and key not in model.bins:
                    errors.setdefault(f.id, f"unknown field {ref.name!r}")
        direct[f.id] = tuple(dict.fromkeys(deps))
        params[f.id] = tuple(dict.fromkeys(ps))

    order: list[str] = []
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(fid: str, path: tuple[str, ...]) -> None:
        if state.get(fid) == 2 or fid not in direct:
            return
        if state.get(fid) == 1:
            for member in path[path.index(fid) :]:
                errors.setdefault(
                    member, "circular reference: " + " → ".join((*path[path.index(fid) :], fid))
                )
            return
        state[fid] = 1
        for dep in direct[fid]:
            visit(dep, (*path, fid))
        state[fid] = 2
        order.append(fid)

    for fid in direct:
        visit(fid, ())

    kinds: dict[str, str] = {}
    rank = {"row": 0, "lod": 1, "aggregate": 2, "table_calc": 3}
    for fid in order:
        if fid in errors:
            kinds[fid] = "invalid"
            continue
        own = classify(trees[fid])
        inherited = [kinds[d] for d in direct[fid] if d in kinds and kinds[d] != "invalid"]
        if any(kinds.get(d) == "invalid" for d in direct[fid]):
            errors[fid] = "depends on an invalid calculation"
            kinds[fid] = "invalid"
            continue
        # Aggregates inside an LOD body stay inside it; anything else propagates upward.
        kind: str = own
        for k in inherited:
            if k == "aggregate" and own == "lod":
                continue
            if rank[k] > rank[kind]:
                kind = k
        kinds[fid] = kind
    for fid in direct:  # members of cycles never reach ``order``
        kinds.setdefault(fid, "invalid")
    ordered = order + [fid for fid in direct if fid not in order]
    return [
        CalcInfo(fid, model.fields[fid].name, kinds[fid], direct[fid], params[fid], errors.get(fid))
        for fid in ordered
    ]
