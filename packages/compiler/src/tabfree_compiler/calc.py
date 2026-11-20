"""Parser for Tableau's calculation language, producing a small immutable AST.

The operator precedence and token rules follow the vendored Apache-2.0 grammar in
``third_party/tableau-calc-grammar/TableauCalculationGrammar.g4`` and Tableau's documentation
(unary minus binds tighter than ``^``; comparisons bind tighter than ``NOT``, then ``AND``, then ``OR``).
Formulas are untrusted input: the parser builds data only and never evaluates anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal as TypingLiteral

LiteralKind = TypingLiteral["string", "integer", "float", "boolean", "null", "date"]
LodScope = TypingLiteral["fixed", "include", "exclude"]

MAX_FORMULA_LENGTH = 64 * 1024
MAX_DEPTH = 64  # each level costs about ten Python frames


class CalcSyntaxError(ValueError):
    """The formula is not valid Tableau calculation syntax."""

    def __init__(self, message: str, formula: str, position: int) -> None:
        super().__init__(f"{message} at position {position}")
        self.formula = formula
        self.position = position


@dataclass(frozen=True)
class Lit:
    kind: LiteralKind
    value: str | int | float | bool | None


@dataclass(frozen=True)
class Field:
    """A bracketed reference. ``parts`` has one entry for ``[Sales]``, two for ``[Parameters].[Top N]``."""

    parts: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.parts[-1]


@dataclass(frozen=True)
class Unary:
    op: str  # "-" or "NOT"
    operand: Node


@dataclass(frozen=True)
class Binary:
    op: str  # + - * / % ^ = <> < <= > >= AND OR
    left: Node
    right: Node


@dataclass(frozen=True)
class Call:
    name: str  # upper case
    args: tuple[Node, ...]


@dataclass(frozen=True)
class If:
    branches: tuple[tuple[Node, Node], ...]
    otherwise: Node | None


@dataclass(frozen=True)
class Case:
    subject: Node
    whens: tuple[tuple[Node, Node], ...]
    otherwise: Node | None


@dataclass(frozen=True)
class InList:
    value: Node
    items: tuple[Node, ...]


@dataclass(frozen=True)
class Lod:
    """``{FIXED [a], [b] : SUM([x])}``; a scope of None is ``{SUM([x])}`` (table-scoped FIXED)."""

    scope: LodScope | None
    dims: tuple[Node, ...]
    body: Node


Node = Lit | Field | Unary | Binary | Call | If | Case | InList | Lod

KEYWORDS = frozenset(
    [
        "AND",
        "OR",
        "NOT",
        "IF",
        "THEN",
        "ELSEIF",
        "ELSE",
        "END",
        "CASE",
        "WHEN",
        "IN",
        "TRUE",
        "FALSE",
        "NULL",
        "FIXED",
        "INCLUDE",
        "EXCLUDE",
    ]
)

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+|//[^\r\n]*|/\*.*?\*/)
  | (?P<field>\[(?:[^\]]|\]\])*\](?:\.\[(?:[^\]]|\]\])*\])*)
  | (?P<string>"(?:[^"]|"")*"|'(?:[^']|'')*')
  | (?P<date>\#[^#\r\n]*\#)
  | (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<ident>[^\W\d]\w*)
  | (?P<op><>|!=|<=|>=|==|[-+*/%^=<>(),{}:])
    """,
    re.VERBOSE | re.DOTALL,
)
_BRACKET = re.compile(r"\[((?:[^\]]|\]\])*)\]")


@dataclass(frozen=True)
class _Token:
    kind: str  # field string date number ident keyword op eof
    text: str
    pos: int


def _tokenize(formula: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(formula):
        match = _TOKEN.match(formula, pos)
        if match is None:
            raise CalcSyntaxError(f"unexpected character {formula[pos]!r}", formula, pos)
        kind = match.lastgroup or ""
        text = match.group()
        if kind == "ident" and text.upper() in KEYWORDS:
            kind, text = "keyword", text.upper()
        if kind != "ws":
            tokens.append(_Token(kind, text, pos))
        pos = match.end()
    tokens.append(_Token("eof", "", len(formula)))
    return tokens


_COMPARISON = {"=": "=", "==": "=", "<>": "<>", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


class _Parser:
    def __init__(self, formula: str) -> None:
        self.formula = formula
        self.tokens = _tokenize(formula)
        self.i = 0
        self.depth = 0

    # -- helpers -------------------------------------------------------------------------------
    @property
    def tok(self) -> _Token:
        return self.tokens[self.i]

    def error(self, message: str) -> CalcSyntaxError:
        return CalcSyntaxError(message, self.formula, self.tok.pos)

    def at(self, kind: str, text: str | None = None) -> bool:
        return self.tok.kind == kind and (text is None or self.tok.text == text)

    def accept(self, kind: str, text: str | None = None) -> _Token | None:
        if self.at(kind, text):
            token = self.tok
            self.i += 1
            return token
        return None

    def expect(self, kind: str, text: str | None = None) -> _Token:
        token = self.accept(kind, text)
        if token is None:
            wanted = text or kind
            got = self.tok.text or "end of formula"
            raise self.error(f"expected {wanted!r}, found {got!r}")
        return token

    # -- grammar -------------------------------------------------------------------------------
    def parse(self) -> Node:
        if self.at("eof"):
            raise self.error("empty formula")
        node = self.expr()
        if not self.at("eof"):
            raise self.error(f"unexpected {self.tok.text!r}")
        return node

    def expr(self) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.error("formula is nested too deeply")
        try:
            return self.or_expr()
        finally:
            self.depth -= 1

    def or_expr(self) -> Node:
        node = self.and_expr()
        while self.accept("keyword", "OR"):
            node = Binary("OR", node, self.and_expr())
        return node

    def and_expr(self) -> Node:
        node = self.not_expr()
        while self.accept("keyword", "AND"):
            node = Binary("AND", node, self.not_expr())
        return node

    def not_expr(self) -> Node:
        if self.accept("keyword", "NOT"):
            return Unary("NOT", self.not_expr())
        return self.comparison()

    def comparison(self) -> Node:
        node = self.additive()
        while True:
            if self.tok.kind == "op" and self.tok.text in _COMPARISON:
                op = _COMPARISON[self.expect("op").text]
                node = Binary(op, node, self.additive())
            elif self.accept("keyword", "IN"):
                self.expect("op", "(")
                items: list[Node] = []
                if not self.at("op", ")"):
                    items.append(self.expr())
                    while self.accept("op", ","):
                        items.append(self.expr())
                self.expect("op", ")")
                node = InList(node, tuple(items))
            else:
                return node

    def additive(self) -> Node:
        node = self.multiplicative()
        while self.tok.kind == "op" and self.tok.text in "+-":
            op = self.expect("op").text
            node = Binary(op, node, self.multiplicative())
        return node

    def multiplicative(self) -> Node:
        node = self.power()
        while self.tok.kind == "op" and self.tok.text in "*/%":
            op = self.expect("op").text
            node = Binary(op, node, self.power())
        return node

    def power(self) -> Node:
        node = self.unary()
        while self.accept("op", "^"):
            node = Binary("^", node, self.unary())
        return node

    def unary(self) -> Node:
        if self.accept("op", "-"):
            operand = self.unary()
            if (
                isinstance(operand, Lit)
                and isinstance(operand.value, int | float)
                and operand.kind != "boolean"
            ):
                return Lit(operand.kind, -operand.value)
            return Unary("-", operand)
        if self.accept("op", "+"):
            return self.unary()
        return self.primary()

    def primary(self) -> Node:
        tok = self.tok
        if tok.kind == "number":
            self.i += 1
            if re.fullmatch(r"\d+", tok.text):
                return Lit("integer", int(tok.text))
            return Lit("float", float(tok.text))
        if tok.kind == "string":
            self.i += 1
            quote = tok.text[0]
            return Lit("string", tok.text[1:-1].replace(quote * 2, quote))
        if tok.kind == "date":
            self.i += 1
            return Lit("date", tok.text[1:-1].strip())
        if tok.kind == "field":
            self.i += 1
            parts = tuple(m.replace("]]", "]") for m in _BRACKET.findall(tok.text))
            return Field(parts)
        if tok.kind == "keyword":
            return self.keyword_primary()
        if tok.kind == "ident":
            self.i += 1
            if self.accept("op", "("):
                args: list[Node] = []
                if not self.at("op", ")"):
                    args.append(self.expr())
                    while self.accept("op", ","):
                        args.append(self.expr())
                self.expect("op", ")")
                return Call(tok.text.upper(), tuple(args))
            # Tableau accepts bare identifiers for simple field names.
            return Field((tok.text,))
        if self.accept("op", "("):
            node = self.expr()
            self.expect("op", ")")
            return node
        if self.accept("op", "{"):
            return self.lod()
        raise self.error(f"unexpected {tok.text or 'end of formula'!r}")

    def keyword_primary(self) -> Node:
        word = self.expect("keyword").text
        if word == "TRUE":
            return Lit("boolean", True)
        if word == "FALSE":
            return Lit("boolean", False)
        if word == "NULL":
            return Lit("null", None)
        if word == "IF":
            branches = [(self.expr(), self._then())]
            otherwise: Node | None = None
            while True:
                if self.accept("keyword", "ELSEIF"):
                    branches.append((self.expr(), self._then()))
                elif self.accept("keyword", "ELSE"):
                    # "ELSE IF ... END" is a nested IF, not ELSEIF; it needs its own END.
                    otherwise = self.expr()
                    self.expect("keyword", "END")
                    break
                else:
                    self.expect("keyword", "END")
                    break
            return If(tuple(branches), otherwise)
        if word == "CASE":
            subject = self.expr()
            whens: list[tuple[Node, Node]] = []
            while self.accept("keyword", "WHEN"):
                whens.append((self.expr(), self._then()))
            if not whens:
                raise self.error("CASE needs at least one WHEN")
            otherwise = self.expr() if self.accept("keyword", "ELSE") else None
            self.expect("keyword", "END")
            return Case(subject, tuple(whens), otherwise)
        self.i -= 1
        raise self.error(f"unexpected keyword {word}")

    def _then(self) -> Node:
        self.expect("keyword", "THEN")
        return self.expr()

    def lod(self) -> Node:
        scope: LodScope | None = None
        dims: list[Node] = []
        for word, value in (("FIXED", "fixed"), ("INCLUDE", "include"), ("EXCLUDE", "exclude")):
            if self.accept("keyword", word):
                scope = value  # type: ignore[assignment]
                break
        if scope is not None:
            if not self.at("op", ":"):
                dims.append(self.expr())
                while self.accept("op", ","):
                    dims.append(self.expr())
            self.expect("op", ":")
        body = self.expr()
        self.expect("op", "}")
        return Lod(scope, tuple(dims), body)


def parse(formula: str) -> Node:
    """Parse a Tableau calculation into an AST. Raises :class:`CalcSyntaxError`."""
    if len(formula) > MAX_FORMULA_LENGTH:
        raise CalcSyntaxError("formula is too long", formula[:80], 0)
    return _Parser(formula).parse()


# ---------------------------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------------------------

AGGREGATES = frozenset(
    [
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "COUNT",
        "COUNTD",
        "MEDIAN",
        "ATTR",
        "STDEV",
        "STDEVP",
        "VAR",
        "VARP",
        "PERCENTILE",
        "COLLECT",
    ]
)
#: MIN and MAX with two arguments are row-level, like LEAST and GREATEST.
TABLE_CALCS = frozenset(
    [
        "INDEX",
        "FIRST",
        "LAST",
        "SIZE",
        "LOOKUP",
        "PREVIOUS_VALUE",
        "RANK",
        "RANK_DENSE",
        "RANK_MODIFIED",
        "RANK_PERCENTILE",
        "RANK_UNIQUE",
        "RUNNING_AVG",
        "RUNNING_COUNT",
        "RUNNING_MAX",
        "RUNNING_MIN",
        "RUNNING_SUM",
        "TOTAL",
        "WINDOW_AVG",
        "WINDOW_CORR",
        "WINDOW_COUNT",
        "WINDOW_COVAR",
        "WINDOW_COVARP",
        "WINDOW_MAX",
        "WINDOW_MEDIAN",
        "WINDOW_MIN",
        "WINDOW_PERCENTILE",
        "WINDOW_STDEV",
        "WINDOW_STDEVP",
        "WINDOW_SUM",
        "WINDOW_VAR",
        "WINDOW_VARP",
    ]
)
USER_FUNCTIONS = frozenset(
    ["USERNAME", "FULLNAME", "USERDOMAIN", "ISMEMBEROF", "ISUSERNAME", "ISFULLNAME", "USERATTRIBUTE"]
)


def is_aggregate_call(node: Node) -> bool:
    if not isinstance(node, Call) or node.name not in AGGREGATES:
        return False
    return not (node.name in ("MIN", "MAX") and len(node.args) == 2)


def children(node: Node) -> tuple[Node, ...]:
    """Direct sub-expressions (LOD bodies included)."""
    match node:
        case Unary(_, operand):
            return (operand,)
        case Binary(_, left, right):
            return (left, right)
        case Call(_, args):
            return args
        case If(branches, otherwise):
            flat = [n for pair in branches for n in pair]
            return (*flat, *([otherwise] if otherwise is not None else []))
        case Case(subject, whens, otherwise):
            flat = [n for pair in whens for n in pair]
            return (subject, *flat, *([otherwise] if otherwise is not None else []))
        case InList(value, items):
            return (value, *items)
        case Lod(_, dims, body):
            return (*dims, body)
        case _:
            return ()


def walk(node: Node) -> list[Node]:
    out = [node]
    for child in children(node):
        out.extend(walk(child))
    return out


def fields_in(node: Node) -> list[Field]:
    return [n for n in walk(node) if isinstance(n, Field)]


Kind = TypingLiteral["row", "aggregate", "lod", "table_calc"]


def classify(node: Node) -> Kind:
    """Where a calculation is evaluated, before resolving calculated fields it references.

    ``table_calc`` wins over ``aggregate``; an expression whose top-level value is an LOD is ``lod``.
    """
    nodes = walk(node)
    if any(isinstance(n, Call) and n.name in TABLE_CALCS for n in nodes):
        return "table_calc"
    if _has_outer_aggregate(node):
        return "aggregate"
    if any(isinstance(n, Lod) for n in nodes):
        return "lod"
    return "row"


def _has_outer_aggregate(node: Node) -> bool:
    """An aggregate outside any LOD body (aggregates inside ``{...}`` do not make the calc aggregate)."""
    if is_aggregate_call(node):
        return True
    if isinstance(node, Lod):
        return False
    return any(_has_outer_aggregate(c) for c in children(node))


def to_formula(node: Node) -> str:
    """Render an AST back to Tableau syntax (fully parenthesised, for diagnostics and cache keys)."""
    match node:
        case Lit("string", value):
            return '"' + str(value).replace('"', '""') + '"'
        case Lit("date", value):
            return f"#{value}#"
        case Lit("null", _):
            return "NULL"
        case Lit("boolean", value):
            return "TRUE" if value else "FALSE"
        case Lit(_, value):
            return repr(value)
        case Field(parts):
            return ".".join("[" + p.replace("]", "]]") + "]" for p in parts)
        case Unary(op, operand):
            return f"({op} {to_formula(operand)})" if op == "NOT" else f"(-{to_formula(operand)})"
        case Binary(op, left, right):
            return f"({to_formula(left)} {op} {to_formula(right)})"
        case Call(name, args):
            return f"{name}({', '.join(to_formula(a) for a in args)})"
        case If(branches, otherwise):
            pieces = []
            for i, (cond, value) in enumerate(branches):
                pieces.append(f"{'IF' if i == 0 else 'ELSEIF'} {to_formula(cond)} THEN {to_formula(value)}")
            if otherwise is not None:
                pieces.append(f"ELSE {to_formula(otherwise)}")
            return " ".join(pieces) + " END"
        case Case(subject, whens, otherwise):
            text = f"CASE {to_formula(subject)}"
            for cond, value in whens:
                text += f" WHEN {to_formula(cond)} THEN {to_formula(value)}"
            if otherwise is not None:
                text += f" ELSE {to_formula(otherwise)}"
            return text + " END"
        case InList(value, items):
            return f"({to_formula(value)} IN ({', '.join(to_formula(i) for i in items)}))"
        case Lod(scope, dims, body):
            head = f"{scope.upper()} {', '.join(to_formula(d) for d in dims)} : " if scope else ""
            return "{" + head + to_formula(body) + "}"
    raise TypeError(f"not a calc node: {node!r}")
