"""The Trino engine against a fake transport (the real run is on the local stack, infra/docker)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

import pytest

from tabfree_api.conformance import load_trino_calcs
from tabfree_api.engines import EngineError, EngineTimeoutError, ExecuteOptions, TrinoEngine
from tabfree_api.main import _engines, create_app
from tabfree_api.settings import Settings
from tabfree_compiler import CompiledQuery, OutputColumn


def compiled(sql: str = "SELECT 1", params: tuple[Any, ...] = ()) -> CompiledQuery:
    return CompiledQuery(
        sql=sql,
        dialect="trino",
        parameters=params,
        named_parameters={},
        columns=(OutputColumn("x", "measure", "integer"),),
    )


class FakeTrino:
    """Serves the given pages in order: the first for the POST, the rest for each GET."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, bytes | None, Mapping[str, str]]] = []

    def __call__(self, method: str, url: str, body: bytes | None, headers: Mapping[str, str]) -> Any:
        self.calls.append((method, url, body, headers))
        return None if method == "DELETE" else self.pages.pop(0)


COLUMNS = [
    {"name": "s", "type": "varchar"},
    {"name": "n", "type": "bigint"},
    {"name": "x", "type": "decimal(12,2)"},
    {"name": "d", "type": "date"},
    {"name": "t", "type": "timestamp(6)"},
    {"name": "b", "type": "boolean"},
]


def test_binds_parameters_pages_and_types_results() -> None:
    fake = FakeTrino(
        [
            {"nextUri": "http://trino:8080/v1/statement/queued/q1/1"},
            {"nextUri": "http://trino:8080/v1/statement/executing/q1/2", "columns": COLUMNS},
            {
                "nextUri": "http://trino:8080/v1/statement/executing/q1/3",
                "data": [["a", 1, "2.50", "2024-01-31", "2024-01-31 10:15:00.000000", True]],
            },
            {"data": [[None, None, None, None, None, None]], "stats": {"processedBytes": 4096}},
        ]
    )
    engine = TrinoEngine(url="http://trino:8080/", schema="lake", transport=fake)
    result = engine.execute(compiled("SELECT 'x' WHERE a = ? AND b = ?", ("O'Brien", 7)), 100)

    assert result.rows == [
        ["a", 1, 2.5, dt.date(2024, 1, 31), dt.datetime(2024, 1, 31, 10, 15), True],
        [None] * 6,
    ]
    assert result.scanned_bytes == 4096
    method, url, body, headers = fake.calls[0]
    assert (method, url) == ("POST", "http://trino:8080/v1/statement")
    # Values are literals after USING; the query text only gains doubled quotes.
    assert body == b"EXECUTE IMMEDIATE 'SELECT ''x'' WHERE a = ? AND b = ?' USING 'O''Brien', 7"
    assert headers["X-Trino-Catalog"] == "iceberg"
    assert headers["X-Trino-Schema"] == "lake"
    assert [c[0] for c in fake.calls[1:]] == ["GET", "GET", "GET"]


def test_a_query_without_parameters_is_sent_as_written() -> None:
    fake = FakeTrino([{"columns": [{"name": "x", "type": "integer"}], "data": [[1]]}])
    assert TrinoEngine(url="http://t", transport=fake).execute(compiled(), 10).rows == [[1]]
    assert fake.calls[0][2] == b"SELECT 1"
    assert "X-Trino-Schema" not in fake.calls[0][3]


def test_engine_errors_show_the_first_line_only() -> None:
    fake = FakeTrino([{"error": {"message": "line 1:8: Column 'nope' cannot be resolved\nstack"}}])
    with pytest.raises(EngineError, match=r"Column 'nope' cannot be resolved$"):
        TrinoEngine(url="http://t", transport=fake).execute(compiled(), 10)


def test_stops_the_query_once_it_has_enough_rows() -> None:
    page = {
        "columns": [{"name": "x", "type": "integer"}],
        "data": [[1], [2], [3]],
        "nextUri": "http://t/next",
    }
    fake = FakeTrino([page])
    result = TrinoEngine(url="http://t", transport=fake).execute(compiled(), 2)
    assert result.rows == [[1], [2], [3]]  # max_rows + 1, so the service can flag truncation
    assert fake.calls[-1][:2] == ("DELETE", "http://t/next")


def test_timeout_cancels_the_query() -> None:
    fake = FakeTrino([{"nextUri": "http://t/next", "stats": {"processedBytes": 10}}])
    with pytest.raises(EngineTimeoutError) as caught:
        TrinoEngine(url="http://t", transport=fake).execute(compiled(), 10, ExecuteOptions(timeout_s=-1))
    assert caught.value.scanned_bytes == 10
    assert fake.calls[-1][:2] == ("DELETE", "http://t/next")


def test_the_app_uses_one_trino_engine_per_model_database() -> None:
    settings = Settings(
        environment="test", engine="trino", trino_url="http://trino:8080", trino_catalog="lake"
    )
    engine_for, today = _engines(settings, site=None)  # type: ignore[arg-type]
    a = engine_for({"connection": {"engine": "athena", "database": "sales"}})
    b = engine_for({"connection": {"engine": "athena", "database": "sales"}})
    c = engine_for({"connection": {"engine": "athena", "database": "hr"}})
    assert isinstance(a, TrinoEngine)
    assert a is b
    assert (a.url, a.catalog, a.schema, c.schema) == ("http://trino:8080", "lake", "sales", "hr")
    assert today is None


def test_trino_is_refused_outside_development() -> None:
    with pytest.raises(RuntimeError, match="trino engine has no authentication"):
        create_app(Settings(environment="production", engine="trino"))


def test_calcs_loader_creates_and_fills_the_table() -> None:
    pytest.importorskip("duckdb")
    fake = FakeTrino([{} for _ in range(4)])
    engine = TrinoEngine(url="http://t", schema="tdvt", transport=fake)
    load_trino_calcs(engine, "tdvt")
    sent = [(c[2] or b"").decode() for c in fake.calls]
    assert sent[0] == "CREATE SCHEMA IF NOT EXISTS tdvt"
    assert sent[2].startswith('CREATE TABLE tdvt.calcs ("key" varchar, "num0" double')
    assert sent[3].startswith("INSERT INTO tdvt.calcs VALUES (CAST('key00' AS varchar), ")
    assert sent[3].count("), (") == 16  # Calcs has 17 rows
    with pytest.raises(ValueError, match="invalid database name"):
        load_trino_calcs(engine, "tdvt; DROP")
