"""Data blending (BUILD_PLAN P2.5): Tableau's secondary data sources.

Tableau aggregates each secondary source at the level of the link fields that are in the view, then
left-joins the result to the primary query on those fields: primary rows without a match keep NULL
secondary measures, and secondary members absent from the primary are dropped. Filters on the primary
do not apply to the secondary, except through the link values.

Both sources must live on the same connection (one SQL statement). Blends across engines would run as
two queries joined by the query service; that is not built yet.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

import sqlglot
from sqlglot import exp

from tabfree_compiler.model import CompileError, SemanticModel, UnsupportedError
from tabfree_compiler.sql import CompiledQuery, Dialect, OutputColumn, Session, compile_query


def _parse_query(sql: str, read: str) -> exp.Query:
    tree = sqlglot.parse_one(sql, read=read)
    if not isinstance(tree, exp.Query):
        raise CompileError("compiled SQL is not a query")
    return tree


def _same_connection(a: SemanticModel, b: SemanticModel) -> bool:
    keys = ("engine", "database", "catalog", "schema", "workgroup", "clusterIdentifier")
    return all(a.connection.get(k) == b.connection.get(k) for k in keys)


def compile_blend(
    models: Mapping[str, SemanticModel | Mapping[str, Any]],
    primary: str,
    query: Mapping[str, Any],
    *,
    dialect: Dialect = "athena",
    session: Session | None = None,
    parameters: Mapping[str, Any] | None = None,
    filters: Sequence[Mapping[str, Any]] = (),
) -> CompiledQuery:
    """Compile ``query`` on model alias ``primary`` with its ``blend`` sections as one statement."""
    resolved = {
        k: m if isinstance(m, SemanticModel) else SemanticModel.from_dict(m) for k, m in models.items()
    }
    if primary not in resolved:
        raise CompileError(f"unknown model alias {primary!r}")
    blends = list(query.get("blend", []))
    base_query = {k: v for k, v in query.items() if k != "blend"}
    if not blends:
        return compile_query(
            resolved[primary],
            base_query,
            dialect=dialect,
            session=session,
            parameters=parameters,
            filters=filters,
        )
    if base_query.get("totals") or base_query.get("pivotMeasures") or base_query.get("tableCalcs"):
        raise UnsupportedError("totals, Measure Names and table calcs on blended queries")

    main = compile_query(
        resolved[primary],
        base_query,
        dialect=dialect,
        session=session,
        parameters=parameters,
        filters=filters,
    )
    outputs = {c.name for c in main.columns if c.role == "dimension"}
    read = "trino" if dialect == "athena" else dialect
    tree = exp.select(exp.Column(this=exp.Star(), table=exp.to_identifier("p", quoted=True))).from_(
        _parse_query(main.sql, read).subquery("p")
    )
    positional = list(main.parameters)
    named = dict(main.named_parameters)
    columns = list(main.columns)
    taken = {c.name for c in columns}
    for i, blend in enumerate(blends):
        alias = blend["model"]
        model = resolved.get(alias)
        if model is None:
            raise CompileError(f"blend {i}: unknown model alias {alias!r}")
        if not _same_connection(resolved[primary], model):
            raise UnsupportedError(f"blend {i}: the secondary source is on another connection")
        links = blend["links"]
        for link in links:
            if link["primary"] not in outputs:
                raise CompileError(f"blend {i}: link {link['primary']!r} is not a dimension of the query")
        secondary_query = {
            "dimensions": [{"field": link["secondary"], "as": f"__link{j}"} for j, link in enumerate(links)],
            "measures": blend["measures"],
        }
        secondary = compile_query(
            model, secondary_query, dialect=dialect, session=session, parameters=parameters
        )
        name = f"s{i}"
        on = exp.and_(
            *[
                exp.EQ(
                    this=exp.column(link["primary"], table="p", quoted=True),
                    expression=exp.column(f"__link{j}", table=name, quoted=True),
                )
                for j, link in enumerate(links)
            ]
        )
        tree = tree.join(_parse_query(secondary.sql, read).subquery(name), on=on, join_type="left")
        for column in secondary.columns:
            if column.role != "measure":
                continue
            if column.name in taken:
                raise CompileError(f'blend {i}: output {column.name!r} already exists; rename it with "as"')
            taken.add(column.name)
            tree = tree.select(exp.column(column.name, table=name, quoted=True), append=True)
            columns.append(OutputColumn(column.name, "measure", column.type))
        positional += list(secondary.parameters)  # textual order: primary first, then each secondary
        named.update(secondary.named_parameters)
    return dataclasses.replace(
        main,
        sql=tree.sql(dialect=dialect),
        parameters=tuple(positional),
        named_parameters=named,
        columns=tuple(columns),
    )
