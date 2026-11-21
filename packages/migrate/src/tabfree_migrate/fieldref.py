"""Tableau's field-reference and shelf-expression grammar (BUILD_PLAN P2.1).

Worksheets refer to fields as column instances such as ``[federated.0a1b].[sum:Sales:qk]``:

* an optional data source (``[federated.0a1b]``) and, for object-model row counts, a logical table
  (``[__tableau_internal_object_id__]``);
* the instance name ``[derivation:field:kind]``, where ``derivation`` is ``none``, ``sum``, ``yr``,
  ``tmn``, ``usr`` and so on, ``kind`` is ``nk`` (nominal), ``ok`` (ordinal) or ``qk`` (quantitative),
  and an optional ``:N`` suffix numbers duplicate pills (dual axes);
* quick table calculations add a prefix (``[pcto:sum:Sales:qk]``, ``[rank:avg:Profit:qk]``) and
  forecasts an ``f*`` prefix (``[fVal:sum:Sales:qk]``);
* plain names (``[Parameters].[Parameter 1]``, ``[ds].[Calculation_1]``) and specials such as
  ``[:Measure Names]`` and ``[Multiple Values]`` have no derivation.

Field names may contain colons (``[none:F: GDP (curr $):qk]``) and ``]`` is escaped as ``]]``.

Shelves (rows, columns, pages) combine references with ``/`` (nest), ``*`` (cross) and ``+``
(concatenate), fully parenthesised by Tableau: ``([a] / ([b] + [c]))``. :func:`parse_shelf` returns a
tree of ``{"op": "nest"|"cross"|"concat", "args": [...]}`` nodes with ``{"op": "field", "ref": ...}``
leaves; chains of the same operator are flattened.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "AGGREGATIONS",
    "DERIVATIONS",
    "TABLE_CALC_PREFIXES",
    "FieldRefError",
    "parse_field_ref",
    "parse_shelf",
    "shelf_refs",
    "split_ref",
]


class FieldRefError(ValueError):
    """Text that is not a valid field reference or shelf expression."""


#: Derivation codes in instance names, mapped to the ``derivation`` attribute Tableau writes.
DERIVATIONS: dict[str, str] = {
    "none": "None", "sum": "Sum", "avg": "Avg", "cnt": "Count", "ctd": "CountD", "cntd": "CountD",
    "attr": "Attribute", "usr": "User", "min": "Min", "max": "Max", "med": "Median",
    "std": "Stdev", "stp": "StdevP", "var": "Var", "vrp": "VarP", "kurt": "Kurtosis",
    "skw": "Skewness", "collect": "Collect", "io": "InOut", "hide": "Hide",
    "yr": "Year", "qr": "Quarter", "mn": "Month", "wk": "Week", "dy": "Day", "wd": "Weekday",
    "hr": "Hour", "mi": "Minute", "sc": "Second", "my": "MY", "md": "MDY",
    "tyr": "Year-Trunc", "tqr": "Quarter-Trunc", "tmn": "Month-Trunc", "twk": "Week-Trunc",
    "tdy": "Day-Trunc", "thr": "Hour-Trunc", "tmi": "Minute-Trunc", "tsc": "Second-Trunc",
    "iyr": "ISO-Year", "iqr": "ISO-Quarter", "iwk": "ISO-Week", "iwd": "ISO-Weekday",
    "tiyr": "ISO-Year-Trunc", "tiqr": "ISO-Quarter-Trunc", "tiwk": "ISO-Week-Trunc",
}  # fmt: skip
#: Derivations that aggregate (a measure on the shelf), as opposed to dimension date parts.
AGGREGATIONS = frozenset(
    {"sum", "avg", "cnt", "ctd", "cntd", "attr", "min", "max", "med", "std", "stp", "var", "vrp",
     "kurt", "skw", "collect"}
)  # fmt: skip
#: Quick table calculation prefixes, mapped to the ``type`` of the ``<table-calc>`` they stand for.
TABLE_CALC_PREFIXES: dict[str, str] = {
    "pcto": "PctTotal", "diff": "Difference", "pcdf": "PctDiff", "rank": "Rank", "cum": "CumTotal",
    "pcrk": "PctRank", "win": "WindowTotal", "pcv": "PctValue", "tc": "Custom",
}  # fmt: skip
_KINDS = {"nk": "nominal", "ok": "ordinal", "qk": "quantitative"}
_PERCENTILE = re.compile(r"^prc(\d{1,3})$")
_FORECAST = re.compile(r"^f[A-Z][A-Za-z]*$")
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_OBJECT_TABLE = "__tableau_internal_object_id__"
_SPECIALS = {":Measure Names": "measure-names", ":Measure Values": "measure-values",
             "Multiple Values": "multiple-values", ":Geometry": "geometry"}  # fmt: skip


def _bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _read_ident(text: str, pos: int) -> tuple[str, int]:
    """Read ``[...]`` starting at ``text[pos] == '['``; ``]]`` is an escaped ``]``."""
    out: list[str] = []
    i = pos + 1
    while i < len(text):
        ch = text[i]
        if ch == "]":
            if i + 1 < len(text) and text[i + 1] == "]":
                out.append("]")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise FieldRefError(f"unterminated '[' at offset {pos} in {text!r}")


def _read_ref(text: str, pos: int) -> tuple[list[str], int]:
    parts: list[str] = []
    while True:
        if pos >= len(text) or text[pos] != "[":
            raise FieldRefError(f"expected '[' at offset {pos} in {text!r}")
        name, pos = _read_ident(text, pos)
        parts.append(name)
        if pos < len(text) and text[pos] == "." and pos + 1 < len(text) and text[pos + 1] == "[":
            pos += 1
            continue
        return parts, pos


def split_ref(text: str) -> list[str]:
    """Split ``[a].[b]`` into its unescaped identifiers ``["a", "b"]``."""
    stripped = text.strip()
    parts, end = _read_ref(stripped, 0)
    if end != len(stripped):
        raise FieldRefError(f"unexpected text after field reference: {stripped!r}")
    return parts


def _is_derivation(code: str) -> bool:
    return code in DERIVATIONS or bool(_PERCENTILE.match(code))


def _instance(name: str, ref: dict[str, Any]) -> None:
    """Fill derivation, field, kind... from an instance name (without brackets)."""
    if name in _SPECIALS:
        ref["special"] = _SPECIALS[name]
        ref["name"] = name
        return
    parts = name.split(":")
    kind_at = None
    if len(parts) >= 3 and parts[-1] in _KINDS:
        kind_at = len(parts) - 1
    elif len(parts) >= 4 and parts[-2] in _KINDS and parts[-1].isdigit():
        kind_at = len(parts) - 2
        ref["index"] = int(parts[-1])
    if kind_at is None or not _CODE.match(parts[0]):
        ref["name"] = name
        return
    head = parts[:kind_at]
    ref["kind"] = parts[kind_at]
    code = head[0]
    if len(head) >= 3 and _is_derivation(head[1]) and not _is_derivation(code):
        if code in TABLE_CALC_PREFIXES:
            ref["tableCalc"] = code
            ref["tableCalcType"] = TABLE_CALC_PREFIXES[code]
        elif _FORECAST.match(code):
            ref["forecast"] = code
        else:
            ref["prefix"] = code
        head = head[1:]
        code = head[0]
    ref["derivation"] = code
    if code in DERIVATIONS:
        ref["derivationName"] = DERIVATIONS[code]
    elif match := _PERCENTILE.match(code):
        ref["derivationName"] = "Percentile"
        ref["percentile"] = int(match.group(1))
    if code in AGGREGATIONS or _PERCENTILE.match(code):
        ref["aggregated"] = True
    ref["name"] = ":".join(head[1:])


def parse_field_ref(text: str) -> dict[str, Any]:
    """Parse a field reference such as ``[ds].[sum:Sales:qk]`` into its parts.

    Returns ``ref`` (the text, stripped), and when present ``datasource``, ``table``, ``name`` (the
    unescaped field name), ``field`` (the bracketed column name as data source ``<column>`` elements
    write it, e.g. ``[Sales]``), ``derivation`` (code, e.g. ``sum``), ``derivationName`` (``Sum``),
    ``aggregated``, ``kind`` (``nk``/``ok``/``qk``), ``index`` (the ``:N`` suffix), ``tableCalc`` /
    ``tableCalcType`` (quick table calc prefix), ``forecast``, ``percentile``, ``parameter`` (the
    ``[Parameters]`` data source) and ``special`` (``measure-names``, ``measure-values``...).
    Raises :class:`FieldRefError` on malformed text.
    """
    stripped = text.strip()
    parts = split_ref(stripped)
    ref: dict[str, Any] = {"ref": stripped}
    *qualifiers, last = parts
    if qualifiers and qualifiers[-1] == _OBJECT_TABLE:
        ref["table"] = qualifiers.pop()
    if len(qualifiers) > 1:
        raise FieldRefError(f"too many qualifiers in {stripped!r}")
    if qualifiers:
        ref["datasource"] = qualifiers[0]
        if qualifiers[0] == "Parameters":
            ref["parameter"] = True
    _instance(last, ref)
    name = str(ref.get("name", last))
    ref["field"] = (_bracket(ref["table"]) + "." if "table" in ref else "") + _bracket(name)
    return ref


# --- shelf expressions -------------------------------------------------------------------------------

_OPS = {"+": "concat", "*": "cross", "/": "nest"}
_PRECEDENCE = {"concat": 1, "cross": 2, "nest": 3}


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        ch = text[pos]
        if ch.isspace():
            pos += 1
        elif ch in "()":
            out.append((ch, ch))
            pos += 1
        elif ch in _OPS:
            out.append(("op", _OPS[ch]))
            pos += 1
        elif ch == "[":
            start = pos
            _parts, pos = _read_ref(text, pos)
            out.append(("ref", text[start:pos]))
        else:
            raise FieldRefError(f"unexpected {ch!r} at offset {pos} in shelf {text!r}")
    return out


class _ShelfParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _tokens(text)
        self.pos = 0

    def parse(self) -> dict[str, Any]:
        if not self.tokens:
            raise FieldRefError("empty shelf expression")
        node = self._expr(1)
        if self.pos != len(self.tokens):
            raise FieldRefError(f"unexpected {self.tokens[self.pos][1]!r} in shelf {self.text!r}")
        return node

    def _expr(self, min_precedence: int) -> dict[str, Any]:
        left = self._atom()
        while self.pos < len(self.tokens) and self.tokens[self.pos][0] == "op":
            op = self.tokens[self.pos][1]
            if _PRECEDENCE[op] < min_precedence:
                break
            self.pos += 1
            right = self._expr(_PRECEDENCE[op] + 1)
            args = left["args"] if left.get("op") == op else [left]
            args = args + (right["args"] if right.get("op") == op else [right])
            left = {"op": op, "args": args}
        return left

    def _atom(self) -> dict[str, Any]:
        if self.pos >= len(self.tokens):
            raise FieldRefError(f"shelf {self.text!r} ends early")
        kind, value = self.tokens[self.pos]
        self.pos += 1
        if kind == "ref":
            return {"op": "field", "ref": parse_field_ref(value)}
        if kind == "(":
            node = self._expr(1)
            if self.pos >= len(self.tokens) or self.tokens[self.pos][0] != ")":
                raise FieldRefError(f"missing ')' in shelf {self.text!r}")
            self.pos += 1
            return node
        raise FieldRefError(f"unexpected {value!r} in shelf {self.text!r}")


def parse_shelf(text: str) -> dict[str, Any]:
    """Parse a rows/columns/pages shelf expression into a nest/cross/concat tree."""
    return _ShelfParser(text.strip()).parse()


def shelf_refs(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """The field references of a shelf tree, left to right."""
    if tree.get("op") == "field":
        return [tree["ref"]]
    return [ref for arg in tree.get("args", []) for ref in shelf_refs(arg)]
