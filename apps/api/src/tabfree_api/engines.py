"""Where compiled SQL runs: DuckDB locally, Athena and Redshift (Data API) on AWS (ADR-007).

Every engine takes the compiler's SQL and bind values and returns rows. Viewer identity and filter
values arrive as bind parameters, never as SQL text. The AWS engines import boto3 lazily
(``tabfree-api[aws]``) and accept an injected client, so they are unit-tested with fakes; they have
not yet run against real Athena or Redshift (P1.16 / P2.10).
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tabfree_compiler import CompiledQuery
from tabfree_compiler.sql import Dialect


class EngineError(RuntimeError):
    """The engine rejected or failed the query. Messages are safe to show to the viewer."""


@dataclass(frozen=True)
class EngineResult:
    rows: list[list[Any]]


class Engine(Protocol):
    dialect: Dialect

    def execute(self, query: CompiledQuery, max_rows: int) -> EngineResult: ...


# ---- DuckDB -----------------------------------------------------------------------------------


class DuckDBEngine:
    """An in-process DuckDB database (local development, tests, the preview)."""

    dialect: Dialect = "duckdb"

    def __init__(self, connection: Any) -> None:
        self._con = connection
        self._lock = threading.Lock()

    @classmethod
    def in_memory(cls) -> DuckDBEngine:
        import duckdb

        return cls(duckdb.connect())

    @property
    def connection(self) -> Any:
        return self._con

    def execute(self, query: CompiledQuery, max_rows: int) -> EngineResult:
        with self._lock:
            try:
                cursor = self._con.execute(query.sql, list(query.parameters))
                rows = cursor.fetchmany(max_rows + 1)
            except Exception as exc:  # duckdb raises many error types
                raise EngineError(f"query failed: {str(exc).splitlines()[0]}") from None
        return EngineResult([list(r) for r in rows])


# ---- Athena -----------------------------------------------------------------------------------


def athena_literal(value: Any) -> str:
    """Athena ExecutionParameters are SQL literals passed as strings."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


class AthenaEngine:
    dialect: Dialect = "athena"

    def __init__(
        self,
        *,
        database: str,
        workgroup: str,
        catalog: str = "AwsDataCatalog",
        region: str | None = None,
        client: Any = None,
        timeout_s: float = 120.0,
        poll_s: float = 0.25,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("athena", region_name=region)
        self._client = client
        self.database, self.workgroup, self.catalog = database, workgroup, catalog
        self.timeout_s, self.poll_s = timeout_s, poll_s

    def execute(self, query: CompiledQuery, max_rows: int) -> EngineResult:
        request: dict[str, Any] = {
            "QueryString": query.sql,
            "WorkGroup": self.workgroup,
            "QueryExecutionContext": {"Database": self.database, "Catalog": self.catalog},
        }
        if query.parameters:
            request["ExecutionParameters"] = [athena_literal(v) for v in query.parameters]
        execution_id = self._client.start_query_execution(**request)["QueryExecutionId"]
        deadline = time.monotonic() + self.timeout_s
        while True:
            status = self._client.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"][
                "Status"
            ]
            state = status["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                raise EngineError(f"Athena query {state.lower()}: {status.get('StateChangeReason', '')}")
            if time.monotonic() > deadline:
                self._client.stop_query_execution(QueryExecutionId=execution_id)
                raise EngineError("Athena query timed out")
            time.sleep(self.poll_s)
        rows: list[list[Any]] = []
        token: str | None = None
        first = True
        while len(rows) <= max_rows:
            kwargs: dict[str, Any] = {"QueryExecutionId": execution_id, "MaxResults": 1000}
            if token:
                kwargs["NextToken"] = token
            page = self._client.get_query_results(**kwargs)
            types = [c["Type"] for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
            data = page["ResultSet"]["Rows"]
            if first:
                data = data[1:]  # the first row of the first page repeats the column names
                first = False
            for row in data:
                rows.append(
                    [
                        _athena_value(cell.get("VarCharValue"), t)
                        for cell, t in zip(row["Data"], types, strict=True)
                    ]
                )
            token = page.get("NextToken")
            if not token:
                break
        return EngineResult(rows[: max_rows + 1])


def _athena_value(text: str | None, type_: str) -> Any:
    if text is None:
        return None
    t = type_.lower()
    if t in ("tinyint", "smallint", "integer", "int", "bigint"):
        return int(text)
    if t in ("double", "float", "real") or t.startswith("decimal"):
        return float(text)
    if t == "boolean":
        return text == "true"
    if t == "timestamp":
        return dt.datetime.fromisoformat(text)
    if t == "date":
        return dt.date.fromisoformat(text)
    return text


# ---- Redshift Data API ------------------------------------------------------------------------


class RedshiftDataEngine:
    dialect: Dialect = "redshift"

    def __init__(
        self,
        *,
        database: str,
        workgroup: str | None = None,
        cluster: str | None = None,
        secret_arn: str | None = None,
        region: str | None = None,
        client: Any = None,
        timeout_s: float = 120.0,
        poll_s: float = 0.25,
    ) -> None:
        if (workgroup is None) == (cluster is None):
            raise ValueError("give exactly one of a Serverless workgroup or a provisioned cluster")
        if client is None:
            import boto3

            client = boto3.client("redshift-data", region_name=region)
        self._client = client
        self.database, self.workgroup, self.cluster, self.secret_arn = (
            database,
            workgroup,
            cluster,
            secret_arn,
        )
        self.timeout_s, self.poll_s = timeout_s, poll_s

    def execute(self, query: CompiledQuery, max_rows: int) -> EngineResult:
        request: dict[str, Any] = {"Sql": query.sql, "Database": self.database}
        if self.workgroup:
            request["WorkgroupName"] = self.workgroup
        else:
            request["ClusterIdentifier"] = self.cluster
        if self.secret_arn:
            request["SecretArn"] = self.secret_arn
        if query.named_parameters:
            request["Parameters"] = [_redshift_parameter(k, v) for k, v in query.named_parameters.items()]
        statement_id = self._client.execute_statement(**request)["Id"]
        deadline = time.monotonic() + self.timeout_s
        while True:
            status = self._client.describe_statement(Id=statement_id)
            if status["Status"] == "FINISHED":
                break
            if status["Status"] in ("FAILED", "ABORTED"):
                raise EngineError(f"Redshift query {status['Status'].lower()}: {status.get('Error', '')}")
            if time.monotonic() > deadline:
                self._client.cancel_statement(Id=statement_id)
                raise EngineError("Redshift query timed out")
            time.sleep(self.poll_s)
        rows: list[list[Any]] = []
        token: str | None = None
        while len(rows) <= max_rows:
            kwargs: dict[str, Any] = {"Id": statement_id}
            if token:
                kwargs["NextToken"] = token
            page = self._client.get_statement_result(**kwargs)
            rows.extend([_redshift_value(f) for f in record] for record in page["Records"])
            token = page.get("NextToken")
            if not token:
                break
        return EngineResult(rows[: max_rows + 1])


def _redshift_parameter(name: str, value: Any) -> dict[str, str]:
    if value is None:
        raise EngineError("the Redshift Data API cannot bind NULL parameters")
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return {"name": name, "value": text}


def _redshift_value(cell: Mapping[str, Any]) -> Any:
    if cell.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if key in cell:
            return cell[key]
    return None


def engine_from_connection(connection: Mapping[str, Any], *, region: str | None = None) -> Engine:
    """An AWS engine for a semantic model's ``connection`` block."""
    kind = connection.get("engine")
    if kind == "athena":
        return AthenaEngine(
            database=connection["database"],
            workgroup=connection.get("workgroup", "primary"),
            catalog=connection.get("catalog", "AwsDataCatalog"),
            region=connection.get("region", region),
        )
    if kind == "redshift":
        return RedshiftDataEngine(
            database=connection["database"],
            workgroup=connection.get("workgroup"),
            cluster=connection.get("clusterIdentifier"),
            secret_arn=connection.get("secretArn"),
            region=connection.get("region", region),
        )
    raise EngineError(f"no engine for connection type {kind!r}")


__all__: Sequence[str] = [
    "AthenaEngine",
    "DuckDBEngine",
    "Engine",
    "EngineError",
    "EngineResult",
    "RedshiftDataEngine",
    "athena_literal",
    "engine_from_connection",
]
