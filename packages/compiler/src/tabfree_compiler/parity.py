"""Per-view CSV parity (BUILD_PLAN P2.9, M2): does a TabFree result match Tableau's own export?

Tableau's "summary data" CSV (``views/{id}/data``, captured in P1.5) is compared with the result of
the recreated query. Rows are matched on the dimension columns; measures must be equal (counts and
integer sums exactly, floats within a tolerance). Tableau exports may carry formatted numbers
("$1,234", "12.5%", "(3)"), US dates and Measure Names / Measure Values in long form; all of that is
normalised first.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_NUMBER = re.compile(r"^\(?-?[$€£¥]?\s*-?[\d,]*\.?\d+(?:[eE][+-]?\d+)?\s*%?\)?$")


def parse_number(text: str) -> float | None:
    raw = text.strip()
    if not raw or not _NUMBER.match(raw):
        return None
    negative = (raw.startswith("(") and raw.endswith(")")) or raw.startswith("-") or raw.startswith("$-")
    percent = raw.endswith("%") or raw.endswith("%)")
    digits = re.sub(r"[^\d.eE+-]", "", raw).lstrip("-")
    try:
        value = float(digits)
    except ValueError:
        return None
    if percent:
        value /= 100
    return -value if negative else value


def normalise(value: Any) -> Any:
    """Comparable form of a CSV cell or a result value: None, float, ISO date/time string or text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ").removesuffix(" 00:00:00")
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    if text in ("", "Null", "%null%"):
        return None
    number = parse_number(text)
    if number is not None:
        return number
    for fmt in ("%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.isoformat(sep=" ").removesuffix(" 00:00:00")
    return text


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def unpivot_measures(rows: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    """Turn Tableau's Measure Names / Measure Values long form into one column per measure."""
    if not rows or "Measure Names" not in rows[0] or "Measure Values" not in rows[0]:
        return [dict(r) for r in rows]
    keys = [k for k in rows[0] if k not in ("Measure Names", "Measure Values")]
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        target = merged.setdefault(key, {k: row[k] for k in keys})
        target[row["Measure Names"]] = row["Measure Values"]
    return list(merged.values())


@dataclass
class ParityReport:
    matched: int = 0
    missing: list[tuple[Any, ...]] = field(default_factory=list)  # in Tableau, not in TabFree
    extra: list[tuple[Any, ...]] = field(default_factory=list)  # in TabFree, not in Tableau
    mismatches: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)  # Tableau columns with no TabFree column

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.mismatches or self.unmapped)


def _equal(expected: Any, actual: Any, rel_tol: float, abs_tol: float) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, float) and isinstance(actual, float):
        if expected.is_integer() and actual.is_integer():
            return expected == actual  # counts and integer sums: exact
        return math.isclose(expected, actual, rel_tol=rel_tol, abs_tol=abs_tol)
    return str(expected).casefold() == str(actual).casefold()


def compare(
    expected_csv: str | Sequence[Mapping[str, str]],
    result: Mapping[str, Any],
    *,
    mapping: Mapping[str, str] | None = None,
    rel_tol: float = 1e-6,
    abs_tol: float = 0.005,
) -> ParityReport:
    """Compare a Tableau CSV export with a TabFree result (``{"columns": [...], "rows": [...]}``).

    ``mapping`` renames Tableau columns to TabFree output names where they differ. Dimension columns
    (the result's ``role == "dimension"``) form the row key.
    """
    expected_rows = unpivot_measures(
        read_csv(expected_csv) if isinstance(expected_csv, str) else expected_csv
    )
    columns = [c["name"] for c in result["columns"]]
    roles = {c["name"]: c.get("role", "dimension") for c in result["columns"]}
    report = ParityReport()
    rename = dict(mapping or {})
    if expected_rows:
        for col in expected_rows[0]:
            target = rename.get(col, col)
            if target not in columns:
                match = next((c for c in columns if c.casefold() == target.casefold()), None)
                if match is None:
                    report.unmapped.append(col)
                else:
                    rename[col] = match
    if report.unmapped:
        return report
    dims = [c for c in columns if roles[c] == "dimension"]
    measures = [c for c in columns if roles[c] != "dimension"]
    actual_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in result["rows"]:
        record = dict(zip(columns, row, strict=True))
        actual_by_key[tuple(normalise(record[d]) for d in dims)] = record
    seen: set[tuple[Any, ...]] = set()
    for row in expected_rows:
        record = {rename.get(k, k): v for k, v in row.items()}
        key = tuple(normalise(record.get(d)) for d in dims)
        actual = actual_by_key.get(key)
        if actual is None:
            report.missing.append(key)
            continue
        seen.add(key)
        bad = [
            f"{key} {m}: Tableau {record[m]!r}, TabFree {actual[m]!r}"
            for m in measures
            if m in record and not _equal(normalise(record[m]), normalise(actual[m]), rel_tol, abs_tol)
        ]
        if bad:
            report.mismatches.extend(bad)
        else:
            report.matched += 1
    report.extra = [k for k in actual_by_key if k not in seen]
    return report
