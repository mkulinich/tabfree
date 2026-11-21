"""The AWS engines against fake boto3 clients (no AWS calls; real-engine runs are P1.16/P2.10)."""

from __future__ import annotations

import datetime as dt
import sys
import types
from typing import Any

import pytest

from tabfree_api.engines import (
    AthenaEngine,
    DuckDBEngine,
    EngineError,
    EngineTimeoutError,
    ExecuteOptions,
    RedshiftDataEngine,
    athena_literal,
    engine_from_connection,
)
from tabfree_compiler import CompiledQuery, OutputColumn


def compiled(
    dialect: str, params: tuple[Any, ...] = (), named: dict[str, Any] | None = None
) -> CompiledQuery:
    return CompiledQuery(
        sql="SELECT 1",
        dialect=dialect,  # type: ignore[arg-type]
        parameters=params,
        named_parameters=named or {},
        columns=(OutputColumn("x", "measure", "integer"),),
    )


class FakeAthena:
    def __init__(self, states: list[str], pages: list[dict[str, Any]]) -> None:
        self.states, self.pages, self.calls = states, pages, []  # type: list[Any]

    def start_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start", kwargs))
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        state = self.states.pop(0)
        return {"QueryExecution": {"Status": {"State": state, "StateChangeReason": "boom"}}}

    def stop_query_execution(self, **kwargs: Any) -> None:
        self.calls.append(("stop", kwargs))

    def get_query_results(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("results", kwargs))
        return self.pages.pop(0)


def athena_page(
    rows: list[list[str | None]], token: str | None = None, header: bool = False
) -> dict[str, Any]:
    meta = {
        "ColumnInfo": [
            {"Type": "varchar"},
            {"Type": "bigint"},
            {"Type": "double"},
            {"Type": "date"},
            {"Type": "boolean"},
        ]
    }
    data = (
        [
            {
                "Data": [
                    {"VarCharValue": "region"},
                    {"VarCharValue": "n"},
                    {"VarCharValue": "s"},
                    {"VarCharValue": "d"},
                    {"VarCharValue": "b"},
                ]
            }
        ]
        if header
        else []
    )
    data += [{"Data": [{} if v is None else {"VarCharValue": v} for v in row]} for row in rows]
    page: dict[str, Any] = {"ResultSet": {"ResultSetMetadata": meta, "Rows": data}}
    if token:
        page["NextToken"] = token
    return page


def test_athena_binds_parameters_pages_and_types_results() -> None:
    fake = FakeAthena(
        ["QUEUED", "RUNNING", "SUCCEEDED"],
        [
            athena_page([["East", "3", "1.5", "2024-01-31", "true"]], token="t", header=True),
            athena_page([["West", None, "2", "2024-02-01", "false"]]),
        ],
    )
    engine = AthenaEngine(database="lake", workgroup="wg", client=fake, poll_s=0)
    result = engine.execute(compiled("athena", ("O'Brien", 5, True, None)), max_rows=10)
    start = fake.calls[0][1]
    assert start["ExecutionParameters"] == ["'O''Brien'", "5", "true", "NULL"]
    assert start["WorkGroup"] == "wg"
    assert start["QueryExecutionContext"]["Database"] == "lake"
    assert result.rows == [
        ["East", 3, 1.5, dt.date(2024, 1, 31), True],
        ["West", None, 2.0, dt.date(2024, 2, 1), False],
    ]


def test_athena_failures_and_timeouts() -> None:
    failing = AthenaEngine(database="d", workgroup="w", client=FakeAthena(["FAILED"], []), poll_s=0)
    with pytest.raises(EngineError, match="failed: boom"):
        failing.execute(compiled("athena"), 10)
    slow = FakeAthena(["RUNNING"] * 100, [])
    with pytest.raises(EngineError, match="timed out"):
        AthenaEngine(database="d", workgroup="w", client=slow, poll_s=0, timeout_s=0).execute(
            compiled("athena"), 10
        )
    assert ("stop", {"QueryExecutionId": "q1"}) in slow.calls


class FakeRedshift:
    def __init__(self) -> None:
        self.request: dict[str, Any] = {}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {"Id": "s1"}

    def describe_statement(self, **kwargs: Any) -> dict[str, Any]:
        return {"Status": "FINISHED"}

    def get_statement_result(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "Records": [[{"stringValue": "East"}, {"longValue": 3}, {"isNull": True}, {"booleanValue": True}]]
        }


def test_redshift_data_api_named_parameters() -> None:
    fake = FakeRedshift()
    engine = RedshiftDataEngine(
        database="dev", workgroup="wg", secret_arn="arn:aws:secretsmanager:x", client=fake, poll_s=0
    )
    result = engine.execute(compiled("redshift", named={"p0": "alice", "p1": True}), max_rows=10)
    assert fake.request["Parameters"] == [{"name": "p0", "value": "alice"}, {"name": "p1", "value": "true"}]
    assert fake.request["WorkgroupName"] == "wg"
    assert "ClusterIdentifier" not in fake.request
    assert result.rows == [["East", 3, None, True]]
    with pytest.raises(EngineError, match="NULL"):
        engine.execute(compiled("redshift", named={"p0": None}), 10)
    with pytest.raises(ValueError, match="exactly one"):
        RedshiftDataEngine(database="dev", client=fake)


def test_literals_and_connection_dispatch() -> None:
    assert athena_literal(1.5) == "1.5"
    assert athena_literal("a'b") == "'a''b'"
    with pytest.raises(EngineError, match="no engine"):
        engine_from_connection({"engine": "trino", "catalog": "x"})


# ---- P2.7: result reuse, bytes scanned, timeouts, workgroup per site ---------------------------


class StatsAthena(FakeAthena):
    def __init__(self, states: list[str], pages: list[dict[str, Any]], stats: dict[str, Any]) -> None:
        super().__init__(states, pages)
        self.stats = stats

    def get_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        state = self.states.pop(0)
        return {"QueryExecution": {"Status": {"State": state}, "Statistics": self.stats}}


def test_athena_result_reuse_and_scanned_bytes() -> None:
    fake = StatsAthena(
        ["SUCCEEDED"],
        [athena_page([["East", "3", "1.5", "2024-01-31", "true"]], header=True)],
        {"DataScannedInBytes": 12_345, "ResultReuseInformation": {"ReusedPreviousResult": True}},
    )
    engine = AthenaEngine(database="lake", workgroup="wg", client=fake, poll_s=0)
    options = ExecuteOptions(timeout_s=5, reuse_minutes=45, reuse_tag="abc123")
    result = engine.execute(compiled("athena"), 10, options)
    start = fake.calls[0][1]
    assert start["ResultReuseConfiguration"] == {
        "ResultReuseByAgeConfiguration": {"Enabled": True, "MaxAgeInMinutes": 45}
    }
    assert start["QueryString"] == "/* tabfree:abc123 */ SELECT 1"
    assert "ResultConfiguration" not in start  # the workgroup owns the output location
    assert result.scanned_bytes == 12_345
    assert result.reused is True
    with pytest.raises(ValueError, match="alphanumeric"):
        engine.execute(compiled("athena"), 10, ExecuteOptions(reuse_minutes=1, reuse_tag="*/ DROP"))


def test_athena_without_reuse_sends_the_plain_query() -> None:
    fake = StatsAthena(["SUCCEEDED"], [athena_page([], header=True)], {"DataScannedInBytes": 0})
    result = AthenaEngine(database="d", workgroup="w", client=fake, poll_s=0).execute(
        compiled("athena"), 10, ExecuteOptions(reuse_minutes=0, reuse_tag="abc")
    )
    start = fake.calls[0][1]
    assert start["QueryString"] == "SELECT 1"
    assert "ResultReuseConfiguration" not in start
    assert result.reused is False
    assert result.scanned_bytes == 0


def test_athena_timeout_from_options_cancels_and_reports_scanned_bytes() -> None:
    slow = StatsAthena(["RUNNING"] * 100, [], {"DataScannedInBytes": 777})
    engine = AthenaEngine(database="d", workgroup="w", client=slow, poll_s=0, timeout_s=999)
    with pytest.raises(EngineTimeoutError, match="timed out") as raised:
        engine.execute(compiled("athena"), 10, ExecuteOptions(timeout_s=0))
    assert raised.value.scanned_bytes == 777
    assert ("stop", {"QueryExecutionId": "q1"}) in slow.calls


class SlowRedshift(FakeRedshift):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled: list[str] = []

    def describe_statement(self, **kwargs: Any) -> dict[str, Any]:
        return {"Status": "STARTED"}

    def cancel_statement(self, **kwargs: Any) -> None:
        self.cancelled.append(kwargs["Id"])


def test_redshift_timeout_from_options_cancels_the_statement() -> None:
    fake = SlowRedshift()
    engine = RedshiftDataEngine(database="dev", workgroup="wg", client=fake, poll_s=0, timeout_s=999)
    with pytest.raises(EngineTimeoutError):
        engine.execute(compiled("redshift"), 10, ExecuteOptions(timeout_s=0))
    assert fake.cancelled == ["s1"]


def test_site_workgroup_wins_over_the_models(monkeypatch: pytest.MonkeyPatch) -> None:
    boto3 = types.SimpleNamespace(client=lambda service, region_name=None: object())
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    connection = {"engine": "athena", "database": "lake", "workgroup": "model-wg"}
    site = engine_from_connection(connection, athena_workgroup="site-wg")
    plain = engine_from_connection(connection)
    assert isinstance(site, AthenaEngine)
    assert isinstance(plain, AthenaEngine)
    assert (site.workgroup, plain.workgroup) == ("site-wg", "model-wg")


def test_duckdb_timeout_interrupts_the_query() -> None:
    duckdb = pytest.importorskip("duckdb")
    engine = DuckDBEngine(duckdb.connect())
    slow = CompiledQuery(
        sql="SELECT count(*) FROM range(1000000000) a(i), range(1000000000) b(j) WHERE i + j = -1",
        dialect="duckdb",
        parameters=(),
        named_parameters={},
        columns=(OutputColumn("n", "measure", "integer"),),
    )
    with pytest.raises(EngineTimeoutError):
        engine.execute(slow, 10, ExecuteOptions(timeout_s=0.2))
    assert engine.execute(compiled("duckdb"), 10, ExecuteOptions(timeout_s=5)).rows == [[1]]
