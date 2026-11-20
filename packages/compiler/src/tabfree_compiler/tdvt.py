"""Run Tableau's TDVT expression tests against the compiler, using DuckDB as the execution engine.

TDVT (Tableau's connector test suite, MIT-licensed, vendored in ``corpus/tdvt``) evaluates each
expression over the ``Calcs`` table and lists the distinct results Tableau expects. Expected files may
have numbered variants (``expected.setup.x.1.txt``); a test passes if any variant matches.

DuckDB stands in for Athena and Redshift here: it checks semantics, not dialect syntax. The same tests
run against Trino (Athena's engine) in the local stack and against the real engines in P2.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

from tabfree_compiler.calc import CalcSyntaxError
from tabfree_compiler.model import CompileError, SemanticModel
from tabfree_compiler.sql import Session, compile_expression

CALCS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("key", "string"),
    *((f"num{i}", "float") for i in range(5)),
    *((f"str{i}", "string") for i in range(4)),
    *((f"int{i}", "integer") for i in range(4)),
    *((f"bool{i}", "boolean") for i in range(4)),
    *((f"date{i}", "date") for i in range(4)),
    ("time0", "datetime"),
    ("time1", "string"),  # TIME has no Tableau equivalent; TDVT tests do not use it as a time
    ("datetime0", "datetime"),
    ("datetime1", "string"),
    ("zzz", "string"),
)
_DUCK_TYPES = {
    "string": "VARCHAR",
    "float": "DOUBLE",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}


#: Tests we do not expect to pass, with the reason. Reviewed by hand; keep this list short.
KNOWN_DEVIATIONS: dict[str, str] = {
    "calcs_data.time | time1": "TIME column: Tableau shows it on day 1901-01-31; no time-of-day type yet",
    "date.datename.sow.weekday | DATENAME('week', [datetime0], 'monday')": (
        "stale expected file: Sunday-based despite 'monday' (datename.sow.week has the right answer)"
    ),
    "math.hexbin | HEXBINX([int2],2)": "hexagonal binning arrives with maps (Phase 4)",
    "math.hexbin | HEXBINY([int2],2)": "hexagonal binning arrives with maps (Phase 4)",
    'string.contains.regex | CONTAINS(str1, "A(")': "stale expected file: expects a NULL; str1 has none",
    'string.contains.regex | CONTAINS(str1, "A\\(")': "stale expected file: expects a NULL; str1 has none",
    "string.find.empty | FIND(str1, 'F')": "stale expected file: its setup file tests FIND(x, '')",
    "string.find.empty | FIND(str1, 'f')": "stale expected file: its setup file tests FIND(x, '')",
}


def deviation_key(case: TestCase) -> str:
    """``"<test file without setup./.txt> | <expression>"``, the key of :data:`KNOWN_DEVIATIONS`."""
    name = case.suite.split("/", 1)[-1].removeprefix("setup.").removesuffix(".txt")
    return f"{name} | {case.expression}"


def calcs_model() -> SemanticModel:
    return SemanticModel.from_dict(
        {
            "schema": "tabfree/model@0",
            "id": "tdvt_calcs",
            "connection": {"engine": "athena", "database": "main"},
            "settings": {"weekStart": "sunday"},
            "tables": [
                {
                    "id": "calcs",
                    "source": {"table": "calcs"},
                    "columns": [{"name": n, "type": t} for n, t in CALCS_COLUMNS],
                }
            ],
            "fields": [
                {
                    "id": n,
                    "name": n,
                    "role": "measure" if t in ("float", "integer") else "dimension",
                    "type": t,
                    "table": "calcs",
                    "column": n,
                }
                for n, t in CALCS_COLUMNS
            ],
        }
    )


def load_calcs(con: Any, csv_path: Path) -> None:
    """Create the ``calcs`` table in a DuckDB connection from TDVT's Calcs.csv.

    Unquoted empty fields are NULL; quoted empty strings ("") stay empty strings, as in TDVT's data.
    """
    columns = ", ".join(f"'{n}': '{_DUCK_TYPES[t]}'" for n, t in CALCS_COLUMNS)
    # The column list is built from the constants above, never from input.
    sql = (
        "CREATE TABLE calcs AS SELECT * FROM read_csv(?, header=false, allow_quoted_nulls=false, "  # noqa: S608
        f"columns={{{columns}}})"
    )
    con.execute(
        sql,
        [str(csv_path)],
    )


@dataclass
class TestCase:
    suite: str
    expression: str
    expected: list[list[list[Any]]]  # variants -> tuples -> values


@dataclass
class Result:
    case: TestCase
    passed: bool
    error: str | None = None
    actual: list[list[Any]] = field(default_factory=list)


def _parse_value(text: str | None) -> Any:
    text = (text or "").strip()
    if text == "%null%":
        return None
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text.startswith("#") and text.endswith("#"):
        return ("date", text[1:-1])
    if text in ("true", "false"):
        return text == "true"
    try:
        return float(text)
    except ValueError:
        return text


def load_suite(directory: Path) -> Iterator[TestCase]:
    """Yield the test cases of one TDVT ``exprtests`` directory (e.g. ``standard``)."""
    variants: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(directory.glob("expected.*.txt")):
        base = re.sub(r"\.\d+\.txt$", ".txt", path.name).removeprefix("expected.")
        variants[base].append(path)
    for base, paths in sorted(variants.items()):
        by_test: dict[str, list[list[list[Any]]]] = defaultdict(list)
        for path in paths:
            root = ElementTree.parse(path).getroot()
            if root is None:
                continue
            for test in root.iter("test"):
                name = test.get("name", "")
                tuples = [[_parse_value(v.text) for v in t.iter("value")] for t in test.iter("tuple")]
                by_test[name].append(tuples)
        for name, expected in by_test.items():
            yield TestCase(f"{directory.name}/{base}", name.strip(), expected)


def _normalise(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return ("date", value.isoformat(sep=" ", timespec="microseconds").rstrip("0").rstrip("."))
    if isinstance(value, dt.date):
        return ("date", value.isoformat())
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    if hasattr(value, "is_finite"):  # Decimal
        return float(value)
    return value


def _same(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, tuple) and isinstance(actual, tuple):
        e, a = _date_key(expected[1]), _date_key(actual[1])
        return e == a
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected == actual)
    if isinstance(expected, float) and isinstance(actual, float):
        if math.isnan(expected) or math.isnan(actual):
            return math.isnan(expected) and math.isnan(actual)
        return math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-9)
    if isinstance(expected, float) and isinstance(actual, str):
        return False
    return bool(expected == actual)


def _date_key(text: str) -> str:
    text = text.strip()
    if re.fullmatch(r"\d{2}:\d{2}:\d{2}(\.\d+)?", text):
        text = "1899-12-30 " + text  # Tableau shows times on its day zero without a date
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += " 00:00:00"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, float):
        return (2, round(value, 6))
    if isinstance(value, tuple):
        return (3, _date_key(value[1]))
    return (4, str(value))


def _matches(expected: list[list[Any]], actual: list[list[Any]]) -> bool:
    if len(expected) != len(actual):
        return False
    key = lambda row: [_sort_key(v) for v in row]  # noqa: E731
    for e_row, a_row in zip(sorted(expected, key=key), sorted(actual, key=key), strict=True):
        if len(e_row) != len(a_row) or not all(_same(e, a) for e, a in zip(e_row, a_row, strict=True)):
            return False
    return True


def run(cases: Iterable[TestCase], con: Any, model: SemanticModel | None = None) -> list[Result]:
    model = model or calcs_model()
    session = Session(username="tdvt", today=dt.date(2020, 1, 1))
    results: list[Result] = []
    for case in cases:
        if not any(case.expected):
            continue  # no expected tuples: TDVT records the test as not applicable
        try:
            compiled = compile_expression(model, case.expression, dialect="duckdb", session=session)
            rows = con.execute(compiled.sql, list(compiled.parameters)).fetchall()
        except (CompileError, CalcSyntaxError) as exc:
            results.append(Result(case, False, f"{type(exc).__name__}: {exc}"))
            continue
        except Exception as exc:  # the engine rejected the SQL: a compiler bug to record, not a crash
            results.append(Result(case, False, f"engine: {str(exc).splitlines()[0]}"))
            continue
        actual = [[_normalise(v) for v in row] for row in rows]
        passed = any(_matches(variant, actual) for variant in case.expected)
        results.append(Result(case, passed, None, actual))
    return results


def summarise(results: list[Result]) -> dict[str, tuple[int, int]]:
    by_suite: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        by_suite[r.case.suite][1] += 1
        by_suite[r.case.suite][0] += int(r.passed)
    return {k: (v[0], v[1]) for k, v in sorted(by_suite.items())}
