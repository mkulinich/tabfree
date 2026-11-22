"""The governed query service: dashboard state or a semantic query in, rows out, with the viewer's RLS.

Two entry points share one pipeline:

* **Dashboards** name a query and send filter values; the service binds them to the manifest
  (`tabfree_compiler.dashboard`), so a viewer narrows data only in the ways the author declared.
* **Explore** (P2.7) sends a manifest-style query (dimensions, measures, where, sort, limit) against
  a semantic model the viewer may Connect to; it is checked against the manifest schema's ``query``.

Either way the compiler applies row-level security from the viewer's session (user functions become
bind parameters), then the pipeline looks up the result cache, checks the site's cost guardrails,
runs the SQL on the model's engine with a timeout, and caps rows and bytes. Every query writes one
audit line: who, what, rows, truncation, cache hit or miss, bytes scanned and duration; never values.
"""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from jsonschema import Draft202012Validator

from tabfree_api.cache import CachedResult, CacheStats, Clock, NoCache, ResultCache, cache_key
from tabfree_api.engines import Engine, EngineError, ExecuteOptions
from tabfree_api.guardrails import GuardrailError, Guardrails, SiteLimits
from tabfree_compiler import CompiledQuery, CompileError, SemanticModel, Session, compile_query
from tabfree_compiler.blend import compile_blend
from tabfree_compiler.dashboard import BoundQuery, bind_domain, bind_query
from tabfree_compiler.sql import Dialect
from tabfree_schemas import SchemaKind, load_schema

ROW_LIMIT = 10_000
MAX_BYTES = 16 * 1024 * 1024
_COLUMN_TYPES = {"string", "integer", "float", "decimal", "boolean", "date", "datetime", "geography"}

audit = logging.getLogger("tabfree.audit")

EngineFor = Callable[[Mapping[str, Any]], Engine]
DataVersion = Callable[[str], str]


@dataclass(frozen=True)
class QueryLimits:
    row_limit: int = ROW_LIMIT
    #: Approximate serialized (JSON) size of the rows a result may carry.
    max_bytes: int = MAX_BYTES
    #: Engine timeout per query; None leaves the engine's own default.
    timeout_s: float | None = 60.0
    #: Athena result reuse maximum age in minutes; 0 turns reuse off.
    athena_reuse_minutes: int = 0
    #: Reuse also for queries with bind parameters (RLS values, filters). Off until verified (P2.10).
    athena_reuse_with_parameters: bool = False
    #: Bytes charged to the site budget for an engine that does not report bytes scanned (Redshift).
    redshift_scan_estimate_bytes: int = 100 * 1024 * 1024


@dataclass
class QueryStats:
    cache: CacheStats = field(default_factory=CacheStats)
    engine_queries: int = 0
    refused: int = 0
    rollup_hits: int = 0


@dataclass(frozen=True)
class _Outcome:
    columns: list[dict[str, Any]]
    result: CachedResult
    cached: bool
    #: Table calcs left for the browser engine (SQL mode only).
    deferred: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        # Exactly these keys: the runtime's protocol checks the shape of results it forwards.
        return {"columns": self.columns, "rows": self.result.rows, "truncated": self.result.truncated}


def _default_guardrails() -> Guardrails:
    return Guardrails(lambda _site: SiteLimits(None, 20, 10.0))


class QueryService:
    def __init__(
        self,
        models: Mapping[str, Mapping[str, Any]],
        engine_for: EngineFor,
        *,
        site: str = "default",
        limits: QueryLimits | None = None,
        cache: ResultCache | None = None,
        guardrails: Guardrails | None = None,
        data_version: DataVersion | None = None,
        clock: Clock = time.time,
    ) -> None:
        self._models = models
        self._engine_for = engine_for
        self._compiled: dict[str, SemanticModel] = {}
        self.site = site
        self.limits = limits or QueryLimits()
        self.cache: ResultCache = cache if cache is not None else NoCache()
        self.guardrails = guardrails or _default_guardrails()
        self._data_version = data_version or (lambda _model: "")
        self._clock = clock
        self.stats = QueryStats()
        #: P6.10: route queries that fit a model's rollups to them (see rollups.py).
        self.rollups = True
        #: P6.7: called with each query's metrics (never values), for the usage and performance pages.
        self.recorder: Callable[[dict[str, Any]], None] | None = None
        self._derived: dict[str, Mapping[str, Any]] = {}

    # ---- entry points ---------------------------------------------------------------------------

    def query(
        self,
        manifest: Mapping[str, Any],
        name: str,
        state: Mapping[str, Any],
        selections: Mapping[str, Any],
        session: Session,
    ) -> dict[str, Any]:
        bound = bind_query(manifest, name, state, selections)
        return self._run(bound, session, f"dashboard={manifest.get('id')} query:{name}").payload()

    def export(
        self,
        manifest: Mapping[str, Any],
        name: str,
        state: Mapping[str, Any],
        selections: Mapping[str, Any],
        session: Session,
        *,
        row_limit: int,
        max_bytes: int,
    ) -> tuple[dict[str, Any], list[str]]:
        """A query for download (P3.7): larger caps, and table calcs computed in SQL where they can be.

        Returns the payload and the names of table calcs that only the browser engine can compute.
        """
        bound = bind_query(manifest, name, state, selections)
        limits = replace(self.limits, row_limit=row_limit, max_bytes=max_bytes)
        outcome = self._run(
            bound, session, f"dashboard={manifest.get('id')} export:{name}", limits=limits, table_calcs="sql"
        )
        return outcome.payload(), list(outcome.deferred)

    def domain(
        self, manifest: Mapping[str, Any], filter_name: str, state: Mapping[str, Any], session: Session
    ) -> dict[str, Any]:
        bound = bind_domain(manifest, filter_name, state)
        return self._run(bound, session, f"dashboard={manifest.get('id')} domain:{filter_name}").payload()

    def explore(self, model_id: str, query: Mapping[str, Any], session: Session) -> dict[str, Any]:
        """An ad-hoc semantic query. The caller has already checked Connect on the model."""
        errors = query_errors(query)
        if errors:
            shown = "; ".join(errors[:5])
            raise CompileError(f"invalid query: {shown}")
        if "model" in query:
            raise CompileError("invalid query: the model comes from the URL, not the query")
        bound = BoundQuery(model_id, query, (), {})
        outcome = self._run(bound, session, f"model={model_id} explore")
        return {
            **outcome.payload(),
            "meta": {
                "cache": "hit" if outcome.cached else "miss",
                "truncatedBy": outcome.result.truncated_by,
                "scannedBytes": outcome.result.scanned_bytes,
            },
        }

    # ---- pipeline -------------------------------------------------------------------------------

    def _register(self, derived: Mapping[str, Any]) -> str:
        """A derived model (rollup, set override), keyed by content so a changed model never reuses it."""
        digest = hashlib.sha256(json.dumps(derived, sort_keys=True, default=str).encode()).hexdigest()[:16]
        key_id = f"{derived['id']}@{digest}"
        if key_id not in self._derived:
            if len(self._derived) > 1000:
                self._derived.clear()
                self._compiled = {k: v for k, v in self._compiled.items() if "@" not in k}
            self._derived[key_id] = derived
        return key_id

    def _model(self, model_id: str) -> tuple[SemanticModel, Mapping[str, Any]]:
        doc = self._models.get(model_id) or self._derived.get(model_id)
        if doc is None:
            raise CompileError(f"unknown model {model_id!r}")
        if model_id not in self._compiled:
            self._compiled[model_id] = SemanticModel.from_dict(doc)
        return self._compiled[model_id], doc

    def _run(
        self,
        bound: BoundQuery,
        session: Session,
        what: str,
        *,
        limits: QueryLimits | None = None,
        table_calcs: Literal["runtime", "sql"] = "runtime",
    ) -> _Outcome:
        started = time.perf_counter()
        _model, doc = self._model(bound.model)
        engine = self._engine_for(doc)
        version = self._data_version(bound.model)
        if bound.sets and not bound.query.get("blend"):
            # Set actions (P6.11): the chosen members replace the sets' definitions for this query.
            doc = with_sets(doc, bound.sets)
            bound = replace(bound, model=self._register(doc))
        if self.rollups and doc.get("rollups") and not bound.query.get("blend"):
            from tabfree_api.rollups import choose

            derived = choose(doc, bound.query, bound.filters)
            if derived is not None:
                # Same connection and data version; the derived model carries the RLS policies.
                bound = replace(bound, model=self._register(derived))
                self.stats.rollup_hits += 1
        limits = limits or self.limits
        query = dict(bound.query)
        cap = min(int(query.get("limit", limits.row_limit)), limits.row_limit)
        query["limit"] = cap + 1  # one extra row tells us the result was truncated
        compiled = self._compile(replace(bound, query=query), session, engine.dialect, table_calcs)
        deferred = tuple(compiled.deferred_table_calcs)
        columns = _columns(compiled, doc, bound.query)
        today = session.today or dt.datetime.fromtimestamp(self._clock(), dt.UTC).date()
        key = cache_key(
            compiled,
            model=bound.model,
            connection=doc.get("connection"),
            data_version=version,
            today=today,
            caps=(limits.row_limit, limits.max_bytes),
        )
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache.hits += 1
            self._audit(session, what, cached, "hit", 0, started)
            return _Outcome(columns, cached, cached=True, deferred=deferred)
        self.stats.cache.misses += 1

        try:
            self.guardrails.check_budget(self.site)
            with self.guardrails.slot(self.site):
                self.stats.engine_queries += 1
                try:
                    result = engine.execute(compiled, cap, self._options(compiled, bound.model, version))
                except EngineError as exc:
                    self.guardrails.charge(self.site, self._scanned(engine, exc.scanned_bytes))
                    raise
        except GuardrailError as exc:
            self.stats.refused += 1
            audit.info("query user=%s %s refused=%s", session.username, what, exc.code)
            raise
        scanned = self._scanned(engine, result.scanned_bytes)
        self.guardrails.charge(self.site, scanned)

        # A query's own limit is part of its design (e.g. "top 200"), not a truncation.
        truncated = len(result.rows) > cap and cap == limits.row_limit
        rows, over = _byte_cap([[_json(v) for v in r] for r in result.rows[:cap]], limits.max_bytes)
        value = CachedResult(
            rows=rows,
            truncated=truncated or over,
            truncated_by="bytes" if over else ("rows" if truncated else None),
            scanned_bytes=scanned,
        )
        self.cache.put(key, value)
        self._audit(session, what, value, "reuse" if result.reused else "miss", scanned, started)
        return _Outcome(columns, value, cached=False, deferred=deferred)

    def _compile(
        self,
        bound: BoundQuery,
        session: Session,
        dialect: Dialect,
        table_calcs: Literal["runtime", "sql"] = "runtime",
    ) -> CompiledQuery:
        model, _doc = self._model(bound.model)
        if bound.query.get("blend"):
            # Data blending: every secondary model joins in the same statement (same connection).
            primary = next((a for a, m in bound.models.items() if m == bound.model), bound.model)
            return compile_blend(
                {alias: self._model(model_id)[0] for alias, model_id in bound.models.items()},
                primary,
                bound.query,
                dialect=dialect,
                session=session,
                parameters=bound.parameters,
                filters=bound.filters,
            )
        return compile_query(
            model,
            bound.query,
            dialect=dialect,
            session=session,
            parameters=bound.parameters,
            filters=bound.filters,
            table_calcs=table_calcs,
        )

    def compile_named(
        self, manifest: Mapping[str, Any], name: str, dialect: Dialect, session: Session
    ) -> CompiledQuery:
        """Compile a dashboard query with its default state, without running it (validation, P4.5)."""
        return self._compile(bind_query(manifest, name, {}, {}), session, dialect)

    def _options(self, compiled: CompiledQuery, model: str, version: str) -> ExecuteOptions:
        limits = self.limits
        reuse = limits.athena_reuse_minutes
        has_binds = bool(compiled.parameters or compiled.named_parameters)
        if has_binds and not limits.athena_reuse_with_parameters:
            reuse = 0
        # The tag puts the data version into the query text, so Athena never reuses a pre-refresh result.
        tag = hashlib.sha256(f"{model}\0{version}".encode()).hexdigest()[:16] if reuse else ""
        return ExecuteOptions(timeout_s=limits.timeout_s, reuse_minutes=reuse, reuse_tag=tag)

    def _scanned(self, engine: Engine, reported: int | None) -> int:
        if reported is not None:
            return reported
        return self.limits.redshift_scan_estimate_bytes if engine.dialect == "redshift" else 0

    def _audit(
        self, session: Session, what: str, result: CachedResult, cache: str, scanned: int, started: float
    ) -> None:
        # Who ran what; never the values (they may be sensitive).
        duration_ms = round((time.perf_counter() - started) * 1000)
        if self.recorder is not None:
            try:
                self.recorder(
                    {"user": session.username, "what": what, "rows": len(result.rows), "cache": cache,
                     "scanned": scanned, "duration_ms": duration_ms}
                )  # fmt: skip
            except Exception:  # metrics never break a query
                audit.warning("could not record query metrics", exc_info=True)
        audit.info(
            "query user=%s %s rows=%d truncated=%s cache=%s scanned_bytes=%d duration_ms=%d",
            session.username,
            what,
            len(result.rows),
            result.truncated,
            cache,
            scanned,
            round((time.perf_counter() - started) * 1000),
        )


# ---- helpers --------------------------------------------------------------------------------------


def with_sets(doc: Mapping[str, Any], sets: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """The model with the named sets defined by these members (a set action's choice)."""
    known = {str(st.get("name")) for st in doc.get("sets", []) or []}
    missing = sorted(set(sets) - known)
    if missing:
        raise CompileError(f"the model has no set {missing[0]!r}")
    out = dict(doc)
    out["sets"] = [
        {k: v for k, v in st.items() if k not in ("members", "condition", "top")}
        | {"members": list(sets[str(st.get("name"))])}
        if str(st.get("name")) in sets
        else st
        for st in doc.get("sets", []) or []
    ]
    return out


@functools.cache
def _query_validator() -> Draft202012Validator:
    schema = load_schema(SchemaKind.DASHBOARD_MANIFEST)
    return Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/query"}
    )


def query_errors(query: Any) -> list[str]:
    """Violations of the manifest schema's ``query`` definition; empty when valid."""
    found = sorted(_query_validator().iter_errors(query), key=lambda e: [str(p) for p in e.absolute_path])
    return [
        "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path) + f": {e.message}"
        for e in found
    ]


def _columns(
    compiled: CompiledQuery, doc: Mapping[str, Any], query: Mapping[str, Any]
) -> list[dict[str, Any]]:
    formats = {f["name"]: f["format"] for f in doc.get("fields", []) if f.get("format")}
    for spec in query.get("measures", []):  # a format on the query's measure wins
        if isinstance(spec, Mapping) and spec.get("format"):
            formats[spec.get("as") or spec["field"]] = spec["format"]
    columns: list[dict[str, Any]] = []
    for c in compiled.columns:
        column: dict[str, Any] = {
            "name": c.name,
            "type": c.type if c.type in _COLUMN_TYPES else "string",
            "role": c.role,
        }
        if c.name in formats:
            column["format"] = formats[c.name]
        columns.append(column)
    return columns


def _byte_cap(rows: list[list[Any]], max_bytes: int) -> tuple[list[list[Any]], bool]:
    """Keep the leading rows whose approximate JSON size fits; True when rows were cut."""
    size = 2  # the enclosing brackets
    for i, row in enumerate(rows):
        size += len(json.dumps(row, default=str, separators=(",", ":"))) + 1
        if size > max_bytes:
            return rows[:i], True
    return rows, False


def _json(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value != value:  # NaN
        return None
    if hasattr(value, "as_integer_ratio") and not isinstance(value, int | float):  # Decimal
        return float(value)
    return value
