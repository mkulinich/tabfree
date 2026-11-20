"""The governed query service: dashboard state in, rows out, with the viewer's RLS applied.

The browser names a query and sends filter values; this service binds them to the manifest
(`tabfree_compiler.dashboard`), compiles with the viewer's session (row-level security, user
functions as bind parameters), runs the SQL on the model's engine and caps the result.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Mapping
from typing import Any

from tabfree_api.engines import Engine
from tabfree_compiler import CompileError, SemanticModel, Session, compile_query
from tabfree_compiler.dashboard import BoundQuery, bind_domain, bind_query

ROW_LIMIT = 10_000
_COLUMN_TYPES = {"string", "integer", "float", "decimal", "boolean", "date", "datetime", "geography"}

audit = logging.getLogger("tabfree.audit")

EngineFor = Callable[[Mapping[str, Any]], Engine]


class QueryService:
    def __init__(self, models: Mapping[str, Mapping[str, Any]], engine_for: EngineFor) -> None:
        self._models = models
        self._engine_for = engine_for
        self._compiled: dict[str, SemanticModel] = {}

    def query(
        self,
        manifest: Mapping[str, Any],
        name: str,
        state: Mapping[str, Any],
        selections: Mapping[str, Any],
        session: Session,
    ) -> dict[str, Any]:
        return self._run(manifest, bind_query(manifest, name, state, selections), session, f"query:{name}")

    def domain(
        self, manifest: Mapping[str, Any], filter_name: str, state: Mapping[str, Any], session: Session
    ) -> dict[str, Any]:
        return self._run(
            manifest, bind_domain(manifest, filter_name, state), session, f"domain:{filter_name}"
        )

    def _model(self, model_id: str) -> tuple[SemanticModel, Mapping[str, Any]]:
        doc = self._models.get(model_id)
        if doc is None:
            raise CompileError(f"unknown model {model_id!r}")
        if model_id not in self._compiled:
            self._compiled[model_id] = SemanticModel.from_dict(doc)
        return self._compiled[model_id], doc

    def _run(
        self, manifest: Mapping[str, Any], bound: BoundQuery, session: Session, what: str
    ) -> dict[str, Any]:
        model, doc = self._model(bound.model)
        engine = self._engine_for(doc)
        query = dict(bound.query)
        query["limit"] = min(int(query.get("limit", ROW_LIMIT)), ROW_LIMIT) + 1
        compiled = compile_query(
            model,
            query,
            dialect=engine.dialect,
            session=session,
            parameters=bound.parameters,
            filters=bound.filters,
        )
        result = engine.execute(compiled, ROW_LIMIT)
        truncated = len(result.rows) > ROW_LIMIT
        rows = result.rows[:ROW_LIMIT]
        formats = {f["name"]: f["format"] for f in doc.get("fields", []) if f.get("format")}
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
        # Audit who ran what; never the values (they may be sensitive).
        audit.info(
            "query user=%s dashboard=%s %s rows=%d truncated=%s",
            session.username,
            manifest.get("id"),
            what,
            len(rows),
            truncated,
        )
        return {"columns": columns, "rows": [[_json(v) for v in r] for r in rows], "truncated": truncated}


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
