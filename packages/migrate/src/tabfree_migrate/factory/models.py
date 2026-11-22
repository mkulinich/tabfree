"""Model generation at scale (BUILD_PLAN P5.1): a semantic model for every data source in the estate.

Published data sources become one model each, shared by every workbook that connects to them (an
embedded ``sqlproxy`` connection names the published source by its content URL). Each embedded data
source becomes a model of its own workbook. Connections are remapped with :mod:`.remap`; sources no
rule covers still get a model (on the default database, so the dashboards can be built and reviewed)
and an entry in the decision queue, which lists every data source waiting on the same database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tabfree_migrate.factory.remap import ExtractCatalogue, Resolution, Rules, resolve
from tabfree_migrate.modelgen import GenerationReport, generate_model, snake


@dataclass(frozen=True)
class SourceDoc:
    """One parsed workbook or published data source, with what the inventory knows about it."""

    key: str  # stable id: the workbook or data source LUID, or its file path offline
    ir: Mapping[str, Any]
    name: str = ""
    content_url: str = ""
    project: str | None = None
    owner: str | None = None
    luid: str | None = None
    updated_at: str | None = None


@dataclass
class ModelEntry:
    id: str
    doc: dict[str, Any]
    report: GenerationReport
    resolution: Resolution
    origin: str  # SourceDoc.key
    datasource: str  # the data source's internal name
    published: bool = False
    content_url: str = ""
    project: str | None = None
    owner: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.doc.get("title"),
            "origin": self.origin,
            "datasource": self.datasource,
            "published": self.published,
            "status": self.resolution.status,
            "connection": self.doc.get("connection"),
            "assumptions": self.report.assumptions,
            "unsupported": self.report.unsupported,
            "review": self.report.review,
            "invalidCalcs": self.report.invalid_calcs,
            "schemaErrors": self.report.schema_errors,
            "remapNotes": self.resolution.notes,
        }


@dataclass
class Estate:
    models: dict[str, ModelEntry] = field(default_factory=dict)
    #: (workbook key, data source internal name) → model id.
    by_source: dict[tuple[str, str], str] = field(default_factory=dict)
    by_content_url: dict[str, str] = field(default_factory=dict)
    #: The decision queue: one entry per database, listing the data sources that wait on it.
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    def model_for(self, workbook: str, datasource: Mapping[str, Any]) -> ModelEntry | None:
        mid = self.by_source.get((workbook, str(datasource.get("name"))))
        if mid is None and (datasource.get("connection") or {}).get("class") == "sqlproxy":
            url = str((datasource.get("published") or {}).get("contentUrl") or "")
            mid = self.by_content_url.get(url.lower())
        return self.models.get(mid) if mid else None

    def decision_queue(self) -> list[dict[str, Any]]:
        return sorted(self.decisions.values(), key=lambda d: (-len(d["datasources"]), d["sourceKey"]))


class _Ids:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, text: str) -> str:
        base = snake(text, "m")[:60].strip("_") or "model"
        candidate, n = base, 2
        while candidate in self.used:
            candidate = f"{base}_{n}"
            n += 1
        self.used.add(candidate)
        return candidate


def _apply(doc: dict[str, Any], resolution: Resolution, published: bool, source: SourceDoc) -> None:
    info = dict(doc.get("source") or {"system": "tableau"})
    info["published"] = published
    if source.luid and published:
        info["luid"] = source.luid
    if source.content_url and published:
        info["contentUrl"] = source.content_url
    doc["source"] = info


def _generate(
    source: SourceDoc,
    ds: Mapping[str, Any],
    model_id: str,
    rules: Rules,
    extracts: ExtractCatalogue | None,
    published: bool,
) -> ModelEntry:
    resolution = resolve(ds, rules, extracts=extracts)
    defaults = rules.defaults
    connection = resolution.connection or {
        "engine": str(defaults.get("engine", "athena")),
        "database": str(defaults.get("database", "tabfree_lake")),
    }
    doc, report = generate_model(
        source.ir,
        datasource=str(ds.get("name")),
        model_id=model_id,
        engine=str(connection["engine"]),
        database=str(connection["database"]),
        table_map=resolution.tables or None,
        connection=connection,
    )
    if resolution.status == "decision":
        report.review.append(
            f"waiting on a decision for {resolution.source_key}: the model points at the default database"
        )
    _apply(doc, resolution, published, source)
    return ModelEntry(
        model_id,
        doc,
        report,
        resolution,
        source.key,
        str(ds.get("name")),
        published,
        source.content_url,
        source.project,
        source.owner,
    )


def _queue(estate: Estate, entry: ModelEntry, workbook_name: str) -> None:
    res = entry.resolution
    if res.status != "decision":
        return
    item = estate.decisions.setdefault(
        res.source_key,
        {"sourceKey": res.source_key, "class": res.source_class, "options": res.options, "datasources": []},
    )
    item["datasources"].append({"model": entry.id, "caption": res.caption, "origin": workbook_name})


def generate_estate(
    datasources: Iterable[SourceDoc],
    workbooks: Iterable[SourceDoc],
    rules: Rules,
    *,
    extracts: ExtractCatalogue | None = None,
) -> Estate:
    """Models for every published data source, then for every workbook's embedded data sources."""
    estate = Estate()
    ids = _Ids()
    for source in datasources:
        sources = [d for d in source.ir.get("datasources", []) if d.get("kind") != "parameters"]
        if not sources:
            estate.errors.append({"origin": source.key, "error": "no data source in the file"})
            continue
        ds = sources[0]
        title = source.name or str(ds.get("caption") or ds.get("name") or source.key)
        try:
            entry = _generate(source, ds, ids.take(title), rules, extracts, published=True)
        except Exception as exc:  # one bad file must not stop the estate
            estate.errors.append({"origin": source.key, "error": f"{type(exc).__name__}: {exc}"})
            continue
        estate.models[entry.id] = entry
        estate.by_source[(source.key, entry.datasource)] = entry.id
        if source.content_url:
            estate.by_content_url[source.content_url.lower()] = entry.id
        _queue(estate, entry, title)
    for source in workbooks:
        workbook = source.name or str((source.ir.get("source") or {}).get("file") or source.key)
        for ds in source.ir.get("datasources", []):
            if ds.get("kind") == "parameters":
                continue
            url = ""
            if (ds.get("connection") or {}).get("class") == "sqlproxy":
                url = str((ds.get("published") or {}).get("contentUrl") or "").lower()
                if url in estate.by_content_url:
                    continue
            title = (
                str(ds.get("caption") or url) if url else f"{workbook} {ds.get('caption') or ds.get('name')}"
            )
            try:
                entry = _generate(source, ds, ids.take(title), rules, extracts, published=bool(url))
                if url:
                    # Not harvested: the workbook keeps a copy of the published source's fields, enough
                    # for a model; its tables are unknown until the .tdsx is downloaded.
                    entry.report.review.append(
                        f"published data source {url!r} was not harvested: model built from the copy "
                        f"in {workbook!r}; download the data source to map its tables"
                    )
                    entry.resolution.status = "decision"
                    entry.resolution.source_key = f"sqlproxy://{url}"
                    entry.resolution.source_class = "sqlproxy"
                    entry.resolution.options = ["replicate", "federated", "extract"]
                    estate.by_content_url[url] = entry.id
            except Exception as exc:
                estate.errors.append(
                    {"origin": source.key, "error": f"{ds.get('caption')}: {type(exc).__name__}: {exc}"}
                )
                continue
            estate.models[entry.id] = entry
            estate.by_source[(source.key, entry.datasource)] = entry.id
            _queue(estate, entry, workbook)
    return estate
