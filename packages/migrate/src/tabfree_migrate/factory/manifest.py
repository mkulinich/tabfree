"""Manifest generation (BUILD_PLAN P5.2): a Tableau dashboard → ``tabfree.json``, deterministically.

For every dashboard of a workbook (and every visible worksheet that no dashboard shows, which
Tableau publishes as a view of its own):

- each worksheet zone becomes a **named query** (:func:`tabfree_migrate.worksheets.worksheet_query`
  against the model generated for its primary data source);
- each **filter card** becomes a manifest filter. The worksheet's own filter on that field moves out
  of the query and becomes the filter's default, so viewers start where Tableau started;
- each **parameter control**, and every parameter a query uses, becomes a manifest parameter, named by
  the model parameter's id so the compiler binds it;
- **filter, highlight and URL actions** become manifest actions between the queries.

No model is involved: the same workbook always gives the same manifest. Everything that could not be
expressed is listed in the plan's ``unsupported`` so the fidelity score and the repair agent see it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from tabfree_migrate.factory.models import Estate, ModelEntry, SourceDoc
from tabfree_migrate.fieldref import FieldRefError, parse_field_ref
from tabfree_migrate.modelgen import tableau_literal
from tabfree_migrate.worksheets import DATE_PARTS, DATE_TRUNCS, WorksheetReport, worksheet_query
from tabfree_schemas.bundle import query_outputs

DASHBOARD_SCHEMA = "tabfree/dashboard@0"
_NAME = re.compile(r"[^A-Za-z0-9_]+")
_FILTER_KINDS = {"categorical": "categorical", "range": "range", "relative_date": "relative_date"}
_PARAM_TYPES = {
    "string": "string",
    "integer": "integer",
    "real": "float",
    "float": "float",
    "boolean": "boolean",
    "date": "date",
    "datetime": "datetime",
}


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "dashboard"
    if not value[0].isalnum():
        value = f"d-{value}"
    return value[:100].rstrip("-")


def name(text: str, prefix: str = "q") -> str:
    """A manifest name (queries, filters): ``^[A-Za-z_][A-Za-z0-9_]{0,63}$``."""
    value = _NAME.sub("_", text).strip("_").lower() or prefix
    if not (value[0].isalpha() or value[0] == "_"):
        value = f"{prefix}_{value}"
    return value[:64]


class _Names:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, text: str, prefix: str = "q") -> str:
        base = name(text, prefix)
        candidate, n = base, 2
        while candidate in self.used:
            suffix = f"_{n}"
            candidate = base[: 64 - len(suffix)] + suffix
            n += 1
        self.used.add(candidate)
        return candidate


@dataclass
class SheetPlan:
    worksheet: str
    query: str  # the query's name in the manifest
    model: ModelEntry | None
    spec: dict[str, Any]
    report: WorksheetReport
    marks: list[str]
    zone: Mapping[str, Any] | None = None


@dataclass
class DashboardPlan:
    id: str
    title: str
    workbook: str
    dashboard: str | None  # None for a worksheet published on its own
    manifest: dict[str, Any]
    sheets: list[SheetPlan] = field(default_factory=list)
    zones: list[Mapping[str, Any]] = field(default_factory=list)
    size: Mapping[str, Any] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Zone id → manifest filter or parameter name, for the HTML baseline.
    controls: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: The queries while the manifest is being built (actions check their outputs).
    manifest_queries: dict[str, Any] = field(default_factory=dict)

    def sheet(self, worksheet: str) -> SheetPlan | None:
        return next((s for s in self.sheets if s.worksheet == worksheet), None)


def walk(zones: Sequence[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
    for zone in zones:
        yield zone
        yield from walk(zone.get("children", []) or [])


def _local(ref: str) -> tuple[str | None, str, str]:
    """``[ds].[none:Region:nk]`` → (data source, local name ``[Region]``, derivation)."""
    try:
        parsed = parse_field_ref(ref)
    except FieldRefError:
        return None, ref, ""
    local = str(parsed.get("field") or f"[{parsed.get('name', '')}]")
    return parsed.get("datasource"), local, str(parsed.get("derivation") or "none")


def _param_type(datatype: str) -> str:
    return _PARAM_TYPES.get(datatype, "string")


def _parameter(param: Mapping[str, Any], model_param: Mapping[str, Any] | None) -> dict[str, Any]:
    info = param.get("param") or {}
    type_ = _param_type(str(param.get("datatype", "string")))
    default = model_param.get("default") if model_param else None
    if default is None:
        default = tableau_literal(info.get("value") or (param.get("calculation") or {}).get("formula"))
    if type_ == "integer" and isinstance(default, float) and default.is_integer():
        default = int(default)
    out: dict[str, Any] = {
        "label": str(param.get("caption") or param.get("name", "")).strip("[]")[:200],
        "type": type_,
        "default": default if default is not None else "",
    }
    if info.get("domain") == "list" and info.get("members"):
        values = [tableau_literal(m) for m in info["members"]]
        allowed: dict[str, Any] = {"list": values}
        aliases = info.get("memberAliases") or {}
        if aliases:
            allowed["labels"] = [str(aliases.get(m, tableau_literal(m))) for m in info["members"]]
        out["allowed"] = allowed
    elif info.get("domain") == "range" and info.get("range"):
        rng = info["range"]
        bounds: dict[str, Any] = {}
        for key, attr in (("min", "min"), ("max", "max")):
            if rng.get(attr) is not None:
                bounds[key] = tableau_literal(rng[attr])
        if rng.get("granularity"):
            step = tableau_literal(rng["granularity"])
            if isinstance(step, int | float) and step > 0:
                bounds["step"] = step
        if bounds:
            out["allowed"] = {"range": bounds}
    return out


class _Builder:
    def __init__(self, source: SourceDoc, estate: Estate) -> None:
        self.source = source
        self.ir = source.ir
        self.estate = estate
        self.workbook = source.name or str((self.ir.get("source") or {}).get("file") or source.key)
        self.datasources = {
            str(d.get("name")): d for d in self.ir.get("datasources", []) if d.get("kind") != "parameters"
        }
        self.params = {str(p.get("name")): p for p in self.ir.get("parameters", []) or []}

    def model_of(self, ds_name: str) -> ModelEntry | None:
        ds = self.datasources.get(ds_name)
        if ds is None:
            ds = next((d for d in self.datasources.values() if d.get("caption") == ds_name), None)
        return self.estate.model_for(self.source.key, ds) if ds is not None else None

    def build(
        self, title: str, dashboard: Mapping[str, Any] | None, sheet_names: Sequence[str]
    ) -> DashboardPlan:
        did = slug(f"{self.workbook} {title}")
        plan = DashboardPlan(did, title, self.workbook, dashboard.get("name") if dashboard else None, {})
        plan.zones = list(dashboard.get("zones", []) if dashboard else [])
        plan.size = dict(dashboard.get("size", {}) if dashboard else {})
        queries = _Names()
        aliases: dict[str, str] = {}  # model id → alias
        worksheets = {str(w.get("name")): w for w in self.ir.get("worksheets", [])}
        zone_of = {str(z.get("worksheet")): z for z in walk(plan.zones) if z.get("type") == "viz"}
        for sheet_name in sheet_names:
            ws = worksheets.get(sheet_name)
            if ws is None:
                plan.unsupported.append(f"worksheet {sheet_name!r} is missing from the workbook")
                continue
            sources = [d for d in ws.get("datasources", []) if d != "Parameters"]
            model = self.model_of(sources[0]) if sources else None
            marks = [str(p.get("markClass") or "Automatic") for p in ws.get("panes", []) or []]
            if model is None:
                report = WorksheetReport(unsupported=["no model for its data source"])
                plan.sheets.append(
                    SheetPlan(
                        sheet_name, queries.take(sheet_name), None, {}, report, marks, zone_of.get(sheet_name)
                    )
                )
                continue
            try:
                spec, report = worksheet_query(self.ir, sheet_name, model.report, datasource=sources[0])
            except Exception as exc:
                spec, report = {}, WorksheetReport(unsupported=[f"{type(exc).__name__}: {exc}"])
            if not (spec.get("dimensions") or spec.get("measures")):
                spec = {}  # a query needs fields; the reason is already in the report
            aliases.setdefault(model.id, name(str(model.doc.get("title") or model.id), "m"))
            plan.sheets.append(
                SheetPlan(
                    sheet_name, queries.take(sheet_name), model, spec, report, marks, zone_of.get(sheet_name)
                )
            )
        # One alias per model; unique even when titles collide.
        alias_names = _Names()
        aliases = {mid: alias_names.take(alias, "m") for mid, alias in aliases.items()}
        multi = len(aliases) > 1
        manifest: dict[str, Any] = {
            "schema": DASHBOARD_SCHEMA,
            "id": did,
            "title": title[:200] or did,
            "models": {alias: mid for mid, alias in aliases.items()} or {"data": "unresolved"},
        }
        query_docs: dict[str, Any] = {}
        for sheet in plan.sheets:
            if not sheet.spec:
                plan.unsupported += [f"{sheet.worksheet}: {u}" for u in sheet.report.unsupported]
                continue
            doc = dict(sheet.spec)
            doc["description"] = f"Tableau worksheet {sheet.worksheet!r}"[:500]
            if multi and sheet.model is not None:
                doc["model"] = aliases[sheet.model.id]
            query_docs[sheet.query] = doc
            plan.unsupported += [f"{sheet.worksheet}: {u}" for u in sheet.report.unsupported]
            plan.notes += [f"{sheet.worksheet}: {n}" for n in sheet.report.notes]
        if not query_docs:
            plan.unsupported.append("no worksheet converted to a query")
        manifest["queries"] = query_docs
        plan.manifest_queries = query_docs
        parameters = self._parameters(plan, query_docs)
        filters = self._filters(plan, query_docs, aliases, multi)
        if parameters:
            manifest["parameters"] = parameters
        if filters:
            manifest["filters"] = filters
        actions = self._actions(plan, dashboard)
        if actions:
            manifest["actions"] = actions
        info: dict[str, Any] = {
            "system": "tableau",
            "workbook": self.workbook,
            "tool": "tabfree-migrate factory",
        }
        if self.source.luid:
            info["workbookLuid"] = self.source.luid
        if plan.dashboard:
            info["dashboard"] = plan.dashboard
        manifest["source"] = info
        plan.manifest = manifest
        return plan

    # -- parameters ---------------------------------------------------------------------------------
    def _model_param(self, model: ModelEntry | None, caption: str) -> Mapping[str, Any] | None:
        if model is None:
            return None
        return next(
            (
                p
                for p in model.doc.get("parameters", []) or []
                if str(p.get("name")).casefold() == caption.casefold()
            ),
            None,
        )

    def _parameters(self, plan: DashboardPlan, queries: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        wanted: list[tuple[str, str | None]] = []
        for zone in walk(plan.zones):
            if zone.get("type") == "paramctrl" and zone.get("param"):
                wanted.append((str(zone["param"]), str(zone.get("id"))))
        # Parameters referenced inside queries ({"param": ...}) must be declared too.
        used = set(re.findall(r"'param': '([^']+)'", repr(queries)))
        first_model = next((s.model for s in plan.sheets if s.model is not None), None)
        for ref, zone_id in wanted:
            local = ref.split("].[")[-1].strip("[]")
            param = self.params.get(f"[{local}]")
            if param is None:
                plan.unsupported.append(f"parameter control for unknown parameter {ref}")
                continue
            caption = str(param.get("caption") or local)
            model_param = self._model_param(first_model, caption)
            key = str(model_param["id"]) if model_param else name(caption, "p")
            out[key] = _parameter(param, model_param)
            if zone_id:
                plan.controls[zone_id] = ("parameter", key)
        for key in used - set(out):
            caption = next(
                (
                    str(p.get("name"))
                    for p in (first_model.doc.get("parameters", []) if first_model else [])
                    if p.get("id") == key
                ),
                key,
            )
            param = next((p for p in self.params.values() if str(p.get("caption")) == caption), None)
            if param is not None:
                out[key] = _parameter(param, self._model_param(first_model, caption))
            else:
                plan.unsupported.append(f"query uses parameter {key!r}, which has no definition")
        return out

    # -- filters ------------------------------------------------------------------------------------
    def _filters(
        self, plan: DashboardPlan, queries: dict[str, Any], aliases: Mapping[str, str], multi: bool
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        names = _Names()
        by_field: dict[tuple[str, str], str] = {}
        for zone in walk(plan.zones):
            if zone.get("type") != "filter" or not zone.get("field"):
                continue
            ds_name, local, derivation = _local(str(zone["field"]))
            sheet = plan.sheet(str(zone.get("worksheet") or ""))
            model = sheet.model if sheet else self.model_of(ds_name or "")
            if model is None:
                plan.unsupported.append(f"filter card on {zone['field']}: no model for its data source")
                continue
            field_name = model.report.names.get(local) or local.strip("[]")
            if derivation not in ("none", "", "io", "usr") and not derivation.startswith(
                ("yr", "mn", "qr", "dy", "tdy", "tmn", "tyr", "tqr")
            ):
                plan.unsupported.append(
                    f"filter card on {field_name!r}: aggregate filter ({derivation}) is not converted"
                )
                continue
            key = (model.id, field_name)
            if key in by_field:
                if str(zone.get("id")):
                    plan.controls[str(zone.get("id"))] = ("filter", by_field[key])
                continue
            fname = names.take(field_name, "f")
            by_field[key] = fname
            targets = [
                s.query
                for s in plan.sheets
                if s.model is not None and s.model.id == model.id and s.query in queries
            ]
            spec: dict[str, Any] = {"label": field_name[:200], "field": field_name, "kind": "categorical"}
            default = None
            for target in targets:
                where = queries[target].get("where") or []
                keep = []
                for flt in where:
                    if flt.get("field") == field_name and flt.get("kind") in _FILTER_KINDS:
                        if default is None:
                            default = flt
                        continue
                    keep.append(flt)
                if keep:
                    queries[target]["where"] = keep
                else:
                    queries[target].pop("where", None)
            if default is not None:
                spec["kind"] = _FILTER_KINDS[default["kind"]]
                if default.get("exclude"):
                    spec["exclude"] = True
                if default.get("context"):
                    spec["context"] = True
                if default["kind"] == "categorical" and default.get("members"):
                    spec["default"] = {"members": default["members"]}
                elif default["kind"] == "range":
                    bounds = {k: default[k] for k in ("min", "max") if default.get(k) is not None}
                    if bounds:
                        spec["default"] = bounds
                elif default["kind"] == "relative_date" and default.get("relative"):
                    spec["default"] = {"relative": default["relative"]}
            elif "qk" in str(zone["field"]).rsplit(":", 1)[-1]:
                spec["kind"] = "range"
            if zone.get("mode") in (
                "checkdropdown",
                "checklist",
                "dropdown",
                "radiolist",
                "slider",
                "typein",
            ):
                spec["multi"] = zone.get("mode") in ("checkdropdown", "checklist")
            spec["appliesTo"] = targets or "all"
            if multi:
                spec["model"] = aliases[model.id]
            out[fname] = spec
            if str(zone.get("id")):
                plan.controls[str(zone.get("id"))] = ("filter", fname)
        return out

    # -- actions ------------------------------------------------------------------------------------
    def _sheets_for(self, plan: DashboardPlan, end: Mapping[str, Any] | None) -> list[str]:
        end = end or {}
        if end.get("worksheet"):
            sheets = [str(end["worksheet"])]
        else:
            excluded = set(end.get("excludeSheets") or [])
            sheets = [s.worksheet for s in plan.sheets if s.worksheet not in excluded]
        return [s.query for s in plan.sheets if s.worksheet in sheets and s.spec]

    def _output_for(self, plan: DashboardPlan, ref: str, queries: Sequence[str]) -> str | None:
        """The output name a field reference has in the given queries (date parts included)."""
        _ds, local, derivation = _local(ref if "].[" in ref else f"[x].{ref}")
        model = next((s.model for s in plan.sheets if s.model and s.query in queries), None)
        base = (model.report.names if model else {}).get(local, local.strip("[]"))
        if derivation in DATE_PARTS:
            candidate = f"{DATE_PARTS[derivation].upper()}({base})"
        elif derivation in DATE_TRUNCS:
            candidate = f"{DATE_TRUNCS[derivation].upper()}_TRUNC({base})"
        else:
            candidate = base
        outputs = [query_outputs(plan.manifest_queries.get(q, {})) for q in queries]
        return candidate if outputs and all(candidate in o for o in outputs) else None

    def _action_fields(
        self,
        plan: DashboardPlan,
        action: Mapping[str, Any],
        sources: list[str],
        targets: list[str],
        caption: str,
    ) -> list[Any]:
        fields: list[Any] = []
        for pair in action.get("fields", []) or []:
            s_name = self._output_for(plan, str(pair.get("source", "")), sources)
            t_name = self._output_for(
                plan, str(pair.get("target", "")), [t for t in targets if t not in sources] or targets
            )
            if s_name is None:
                plan.notes.append(
                    f"action {caption!r}: field {pair.get('source')} is not on the source view; left out"
                )
                continue
            t_name = t_name or s_name
            fields.append(s_name if s_name == t_name else {"source": s_name, "target": t_name})
        return fields

    def _actions(self, plan: DashboardPlan, dashboard: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if dashboard is None:
            return out
        for action in self.ir.get("actions", []) or []:
            src = action.get("source") or {}
            if src.get("dashboard") not in (None, dashboard.get("name")) and src.get("worksheet") not in {
                s.worksheet for s in plan.sheets
            }:
                continue
            kind = action.get("type")
            sources = self._sheets_for(plan, src)
            if not sources:
                continue
            caption = str(action.get("caption") or action.get("name") or "")[:200]
            trigger = action.get("trigger", "select")
            trigger = trigger if trigger in ("select", "hover", "menu") else "select"
            if kind in ("filter", "highlight"):
                target = action.get("target") or {}
                if target.get("dashboard") not in (None, dashboard.get("name")):
                    plan.unsupported.append(f"action {caption!r} targets another dashboard (navigation)")
                    continue
                targets = self._sheets_for(plan, target)
                if kind == "filter":
                    targets = [t for t in targets if t not in sources] or targets
                if not targets:
                    continue
                entry: dict[str, Any] = {
                    "type": kind,
                    "name": caption,
                    "source": sources,
                    "targets": targets,
                    "trigger": trigger,
                }
                fields = self._action_fields(plan, action, sources, targets, caption)
                if fields:
                    entry["fields"] = fields
                if kind == "filter":
                    on_empty = str((action.get("clear") or {}).get("onEmpty") or "")
                    entry["clear"] = {"dont-modify": "keep_filter", "exclude": "exclude_all"}.get(
                        on_empty, "show_all"
                    )
                out.append(entry)
            elif kind == "url":
                url = str(
                    (action.get("target") or {}).get("url")
                    or (action.get("link") or {}).get("expression")
                    or ""
                )
                url = re.sub(r"<\[[^\]]+\]\.\[([^\]]+)\]>", lambda m: f"<{m.group(1)}>", unquote(url))
                if not url.lower().startswith(("http://", "https://")):
                    plan.unsupported.append(f"URL action {caption!r}: only http(s) links are kept")
                    continue
                out.append(
                    {
                        "type": "url",
                        "name": caption,
                        "source": sources,
                        "url": url,
                        "trigger": "menu" if trigger == "menu" else trigger,
                    }
                )
            else:
                plan.unsupported.append(f"{kind} action {caption!r} is not converted yet (P6.11)")
        return out


def plan_workbook(
    source: SourceDoc, estate: Estate, *, standalone_sheets: bool = True
) -> list[DashboardPlan]:
    """A plan per dashboard, plus one per visible worksheet that no dashboard contains."""
    builder = _Builder(source, estate)
    plans: list[DashboardPlan] = []
    shown: set[str] = set()
    for dashboard in source.ir.get("dashboards", []) or []:
        sheets = []
        for zone in walk(dashboard.get("zones", []) or []):
            if zone.get("type") == "viz" and zone.get("worksheet") and zone["worksheet"] not in sheets:
                sheets.append(str(zone["worksheet"]))
        shown.update(sheets)
        plans.append(builder.build(str(dashboard.get("name", "Dashboard")), dashboard, sheets))
    if standalone_sheets:
        for ws in source.ir.get("worksheets", []) or []:
            if ws.get("hidden") or ws.get("name") in shown:
                continue
            plans.append(builder.build(str(ws.get("name")), None, [str(ws.get("name"))]))
    seen: set[str] = set()
    for plan in plans:  # dashboard ids are unique within a workbook
        base, n = plan.id, 2
        while plan.id in seen:
            plan.id = f"{base[:96]}-{n}"
            n += 1
        plan.manifest["id"] = plan.id
        seen.add(plan.id)
    return plans
