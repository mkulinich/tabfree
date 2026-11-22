"""Waves (BUILD_PLAN P5.7): which dashboards move first, and what is retired.

Dashboards are ranked by usage (views in the repository's ``views_stats``, or REST ``total_views``):

- **wave 1**: the most used (default 20), migrated with a pilot group;
- **wave 2**: the next ones (default 100);
- **wave 3**: the long tail;
- **retire**: dashboards used fewer than ``retire_below`` times. They are not converted further; their
  workbook and ground truth are archived (:func:`archive`), so the evidence outlives Tableau.

A dashboard that could not be converted at all (no worksheet became a query) stays in its wave and is
marked ``manual`` for a person to rebuild.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tabfree_migrate.blobstore import Sink, load_json, save_json


@dataclass(frozen=True)
class WaveSettings:
    sizes: tuple[int, ...] = (20, 100)
    retire_below: int = 0  # 0: retire nothing automatically

    @classmethod
    def from_document(cls, doc: Mapping[str, Any] | None) -> WaveSettings:
        doc = doc or {}
        sizes = tuple(int(n) for n in doc.get("sizes", (20, 100)))
        if any(n < 0 for n in sizes):
            raise ValueError("wave sizes must be positive")
        return cls(sizes, int(doc.get("retireBelow", 0)))


@dataclass(frozen=True)
class Candidate:
    dashboard: str
    title: str
    usage: int
    converted: bool
    workbook_luid: str | None = None


def plan_waves(candidates: Sequence[Candidate], settings: WaveSettings | None = None) -> dict[str, Any]:
    settings = settings or WaveSettings()
    ranked = sorted(candidates, key=lambda c: (-c.usage, c.title, c.dashboard))
    keep = [c for c in ranked if settings.retire_below <= 0 or c.usage >= settings.retire_below]
    retire = [c for c in ranked if c not in keep]
    waves: list[dict[str, Any]] = []
    start = 0
    for number, size in enumerate(settings.sizes, start=1):
        chunk = keep[start : start + size]
        start += size
        waves.append({"wave": number, "dashboards": [c.dashboard for c in chunk]})
    waves.append({"wave": len(settings.sizes) + 1, "dashboards": [c.dashboard for c in keep[start:]]})
    items = {}
    for wave in waves:
        for did in wave["dashboards"]:
            c = next(x for x in keep if x.dashboard == did)
            items[did] = {
                "wave": wave["wave"],
                "usage": c.usage,
                "status": "converted" if c.converted else "manual",
            }
    for c in retire:
        items[c.dashboard] = {"wave": None, "usage": c.usage, "status": "retire"}
    return {
        "schema": "tabfree/waves@0",
        "settings": {"sizes": list(settings.sizes), "retireBelow": settings.retire_below},
        "waves": waves,
        "retire": [c.dashboard for c in retire],
        "items": items,
    }


ARCHIVE_INDEX = "archive/index.json"


def archive(
    source: Sink,
    target: Sink,
    *,
    dashboard: str,
    workbook_key: str | None,
    ground_truth_keys: Sequence[str],
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy a retired dashboard's workbook and captures to ``archive/<dashboard>/`` in ``target``.

    The copy is content-addressed like the raw zone (same keys under the archive prefix) and an index
    entry records what was kept and why. Nothing is deleted from the source.
    """
    kept: list[str] = []
    missing: list[str] = []
    for key in [k for k in [workbook_key, *ground_truth_keys] if k]:
        data = source.get_bytes(key)
        if data is None:
            missing.append(key)
            continue
        target.put_bytes(f"archive/{dashboard}/{key}", data, "application/octet-stream")
        kept.append(key)
    entry = {
        "dashboard": dashboard,
        "reason": reason,
        "kept": kept,
        "missing": missing,
        "metadata": dict(metadata or {}),
        "archivedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    target.put_bytes(
        f"archive/{dashboard}/archive.json", json.dumps(entry, indent=2).encode(), "application/json"
    )
    index = load_json(target, ARCHIVE_INDEX)
    index.setdefault("entries", {})[dashboard] = entry
    save_json(target, ARCHIVE_INDEX, index)
    return entry
