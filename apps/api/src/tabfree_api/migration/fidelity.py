"""The fidelity score (BUILD_PLAN P5.3): how close a converted dashboard is to its Tableau original.

Four components, each from 0 to 1, when known:

- **coverage**: the share of worksheets the factory converted (a partly converted sheet counts half);
- **validation**: 1 when the draft passes validation, 0.5 with warnings only, 0 when a check fails;
- **data**: the share of captured Tableau CSV rows the TabFree queries reproduce (data parity);
- **visual**: layout similarity between Tableau's PNG and the TabFree render.

The score is their weighted mean over the components that are known (data and visual weigh most), and
an unconverted feature list rides along. The grade drives the console: ``pass`` (ready for review),
``review`` (a person should look closely) or ``fail`` (repair or rebuild).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

WEIGHTS = {"coverage": 0.2, "validation": 0.15, "data": 0.4, "visual": 0.25}
PASS, REVIEW = 0.85, 0.6


def data_score(results: Mapping[str, Mapping[str, Any]]) -> float | None:
    """Parity per query ({matched, missing, extra, mismatches, unmapped}) → share of rows reproduced."""
    matched = total = 0
    for result in results.values():
        if result.get("error"):
            total += max(1, int(result.get("expected", 1)))
            continue
        m = int(result.get("matched", 0))
        expected = m + int(result.get("missing", 0)) + int(result.get("mismatched", 0))
        extra = int(result.get("extra", 0))
        if result.get("unmapped"):
            expected = max(expected, 1)
            m = 0
        matched += m
        total += expected + extra
    return round(matched / total, 4) if total else None


def validation_score(report: Mapping[str, Any] | None) -> float | None:
    if not report:
        return None
    checks = report.get("checks") or []
    if any(c.get("status") == "fail" for c in checks):
        return 0.0
    if any(c.get("status") == "warn" for c in checks):
        return 0.5
    return 1.0


def score(
    *,
    coverage: float | None,
    validation: float | None = None,
    data: float | None = None,
    visual: float | None = None,
    unsupported: Sequence[str] = (),
) -> dict[str, Any]:
    components = {"coverage": coverage, "validation": validation, "data": data, "visual": visual}
    known = {k: v for k, v in components.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in known)
    value = round(sum(WEIGHTS[k] * v for k, v in known.items()) / weight, 4) if weight else 0.0
    if validation == 0.0 or not known:
        grade = "fail"
    elif value >= PASS and data is not None and data >= 0.95:
        grade = "pass"
    elif value >= PASS and data is None:
        grade = "review"  # looks right, but no Tableau data to prove it
    elif value >= REVIEW:
        grade = "review"
    else:
        grade = "fail"
    return {
        "score": value,
        "grade": grade,
        "components": components,
        "unsupported": list(dict.fromkeys(unsupported)),
    }
