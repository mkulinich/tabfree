"""P2.7 query service pipeline: result cache, caps, timeouts, cost guardrails and audit (fake engines)."""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Mapping
from typing import Any

import pytest

from tabfree_api.cache import CachedResult, MemoryResultCache, cache_key
from tabfree_api.content import Site
from tabfree_api.engines import EngineError, EngineResult, ExecuteOptions
from tabfree_api.guardrails import GuardrailError, Guardrails, SiteLimits
from tabfree_api.service import QueryLimits, QueryService, query_errors
from tabfree_api.settings import Settings
from tabfree_compiler import CompiledQuery, CompileError, OutputColumn, Session

MODELS = Site(Settings().site_file).models
TODAY = dt.date(2024, 12, 31)
MANIFEST: dict[str, Any] = {
    "id": "d",
    "models": {"sales": "superstore_sales"},
    "queries": {
        "by_region": {"dimensions": ["Region"], "measures": ["Sales"], "sort": [{"by": "Region"}]},
        "top": {"dimensions": ["Region"], "measures": ["Sales"], "limit": 2},
    },
    "filters": {},
}
EMPTY: dict[str, Any] = {"filters": {}, "parameters": {}}


def manager(name: str) -> Session:
    return Session(username=name, groups=frozenset({"Managers"}), today=TODAY)


def executive(name: str) -> Session:
    return Session(username=name, groups=frozenset({"Executives"}), today=TODAY)


class Clock:
    def __init__(self, start: float = 1_735_603_200.0) -> None:  # 2024-12-31 00:00 UTC
        self.now = start

    def __call__(self) -> float:
        return self.now


class FakeEngine:
    def __init__(
        self,
        dialect: str = "athena",
        rows: list[list[Any]] | None = None,
        scanned: int | None = 1_000,
        fail: EngineError | None = None,
    ) -> None:
        self.dialect = dialect
        self.rows = rows if rows is not None else [["East", 1.0], ["West", 2.0]]
        self.scanned, self.fail = scanned, fail
        self.calls: list[tuple[CompiledQuery, int, ExecuteOptions | None]] = []
        self.gate: threading.Event | None = None
        self.started = threading.Event()

    def execute(
        self, query: CompiledQuery, max_rows: int, options: ExecuteOptions | None = None
    ) -> EngineResult:
        self.calls.append((query, max_rows, options))
        self.started.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail is not None:
            raise self.fail
        return EngineResult(self.rows[: max_rows + 1], scanned_bytes=self.scanned)


def make(
    engine: FakeEngine,
    *,
    limits: QueryLimits | None = None,
    site_limits: SiteLimits | None = None,
    versions: Mapping[str, str] | None = None,
    clock: Clock | None = None,
) -> QueryService:
    clock = clock or Clock()
    versions = versions if versions is not None else {}
    return QueryService(
        MODELS,
        lambda _model: engine,  # type: ignore[arg-type, return-value]
        site="s1",
        limits=limits or QueryLimits(),
        cache=MemoryResultCache(100, 60, clock=clock),
        guardrails=Guardrails(lambda _site: site_limits or SiteLimits(None, 4, 0.0), clock=clock),
        data_version=lambda model: versions.get(model, "v1"),
        clock=clock,
    )


def run(service: QueryService, session: Session, name: str = "by_region") -> dict[str, Any]:
    return service.query(MANIFEST, name, EMPTY, {}, session)


# ---- result cache -------------------------------------------------------------------------------


def test_repeat_queries_hit_the_cache_and_return_the_same_result() -> None:
    engine = FakeEngine()
    service = make(engine)
    first = run(service, manager("alice"))
    second = run(service, manager("alice"))
    assert first == second
    assert set(first) == {"columns", "rows", "truncated"}  # the runtime protocol's exact keys
    assert len(engine.calls) == 1
    assert (service.stats.cache.hits, service.stats.cache.misses) == (1, 1)
    assert service.stats.cache.hit_rate == 0.5


def test_different_rls_bindings_never_share_a_cache_entry() -> None:
    engine = FakeEngine()
    service = make(engine)
    run(service, manager("alice"))
    run(service, manager("bob"))  # same SQL, different USERNAME() binding
    assert len(engine.calls) == 2
    assert engine.calls[0][0].sql == engine.calls[1][0].sql
    assert engine.calls[0][0].parameters != engine.calls[1][0].parameters
    # Executives are exempt from the RLS policy: identical SQL and bindings, so they may share.
    run(service, executive("erin"))
    run(service, executive("ezra"))
    assert len(engine.calls) == 3
    assert service.stats.cache.hits == 1


def test_a_new_data_version_misses_and_ttl_and_lru_expire_entries() -> None:
    engine = FakeEngine()
    clock = Clock()
    versions = {"superstore_sales": "v1"}
    service = make(engine, versions=versions, clock=clock)
    run(service, manager("alice"))
    versions["superstore_sales"] = "v2"  # the refresh pipeline bumps it
    run(service, manager("alice"))
    assert len(engine.calls) == 2
    clock.now += 61  # past the 60 s TTL
    run(service, manager("alice"))
    assert len(engine.calls) == 3

    lru = MemoryResultCache(max_entries=2, ttl_s=60, clock=clock)
    value = CachedResult([], False, None, 0)
    for key in ("a", "b"):
        lru.put(key, value)
    assert lru.get("a") is value  # "a" is now the most recently used
    lru.put("c", value)
    assert lru.get("b") is None
    assert lru.get("a") is value
    assert len(lru) == 2
    with pytest.raises(ValueError, match="at least 1"):
        MemoryResultCache(max_entries=0)


def compiled_with(*params: Any, sql: str = "SELECT ?") -> CompiledQuery:
    return CompiledQuery(sql, "athena", tuple(params), {}, (OutputColumn("x", "measure", "integer"),))


def key_of(query: CompiledQuery, **overrides: Any) -> str:
    args: dict[str, Any] = {
        "model": "m",
        "connection": {"engine": "athena", "database": "lake"},
        "data_version": "v1",
        "today": TODAY,
        "caps": (10, 100),
    }
    args.update(overrides)
    return cache_key(query, **args)


def test_cache_key_covers_sql_typed_bindings_version_and_scope() -> None:
    base = key_of(compiled_with(1))
    assert base == key_of(compiled_with(1))
    variants = [
        key_of(compiled_with(True)),
        key_of(compiled_with("1")),
        key_of(compiled_with(1.0)),
        key_of(compiled_with(None)),
        key_of(compiled_with(1, sql="SELECT ? + 0")),
        key_of(compiled_with(1), data_version="v2"),
        key_of(compiled_with(1), model="other"),
        key_of(compiled_with(1), connection={"engine": "athena", "database": "other"}),
        key_of(compiled_with(1), today=TODAY + dt.timedelta(days=1)),
        key_of(compiled_with(1), caps=(10, 200)),
        key_of(compiled_with(dt.date(2024, 1, 1))),
    ]
    assert base not in variants
    assert len(set(variants)) == len(variants)


# ---- caps and timeouts --------------------------------------------------------------------------


def test_row_cap_truncates_but_a_query_limit_does_not() -> None:
    rows = [[f"r{i}", float(i)] for i in range(10)]
    service = make(FakeEngine(rows=rows), limits=QueryLimits(row_limit=3))
    capped = run(service, executive("erin"))
    assert len(capped["rows"]) == 3
    assert capped["truncated"] is True
    top = run(service, executive("erin"), "top")  # its own limit of 2 is a design, not a truncation
    assert len(top["rows"]) == 2
    assert top["truncated"] is False


def test_byte_cap_cuts_rows_and_flags_the_result() -> None:
    rows = [["x" * 100, 1.0] for _ in range(50)]
    service = make(FakeEngine(rows=rows), limits=QueryLimits(max_bytes=1_000))
    result = service.explore("superstore_sales", {"dimensions": ["Region"]}, executive("erin"))
    assert 0 < len(result["rows"]) < 10
    assert result["truncated"] is True
    assert result["meta"]["truncatedBy"] == "bytes"
    assert result["meta"]["cache"] == "miss"
    again = service.explore("superstore_sales", {"dimensions": ["Region"]}, executive("erin"))
    assert again["meta"]["cache"] == "hit"
    assert again["rows"] == result["rows"]


def test_timeouts_and_reuse_options_reach_the_engine() -> None:
    engine = FakeEngine()
    service = make(engine, limits=QueryLimits(timeout_s=7.5, athena_reuse_minutes=30))
    run(service, executive("erin"))  # no bind parameters: reuse is safe
    run(service, manager("alice"))  # RLS binding: reuse stays off by default
    exec_options, rls_options = engine.calls[0][2], engine.calls[1][2]
    assert exec_options is not None
    assert rls_options is not None
    assert exec_options.timeout_s == 7.5
    assert exec_options.reuse_minutes == 30
    assert exec_options.reuse_tag.isalnum()
    assert len(exec_options.reuse_tag) == 16
    assert rls_options.reuse_minutes == 0
    assert rls_options.reuse_tag == ""

    other = FakeEngine()
    trusting = make(other, limits=QueryLimits(athena_reuse_minutes=30, athena_reuse_with_parameters=True))
    run(trusting, manager("alice"))
    options = other.calls[0][2]
    assert options is not None
    assert options.reuse_minutes == 30


# ---- cost guardrails ----------------------------------------------------------------------------


def test_daily_scan_budget_refuses_new_queries_until_the_utc_day_rolls_over() -> None:
    clock = Clock()
    engine = FakeEngine(scanned=600)
    service = make(engine, site_limits=SiteLimits(1_000, 4, 0.0), clock=clock)
    run(service, manager("alice"))  # 600 of 1000
    run(service, manager("bob"))  # 1200: over budget now
    assert service.guardrails.scanned_today("s1") == 1_200
    with pytest.raises(GuardrailError) as refused:
        run(service, manager("carol"))
    assert refused.value.code == "scan_budget_exceeded"
    assert "daily query budget" in refused.value.message
    assert refused.value.retry_after_s == 86_400
    assert run(service, manager("alice"))["rows"]  # cached results are still served
    assert service.stats.refused == 1
    clock.now += 86_400  # next UTC day
    run(service, manager("carol"))
    assert service.guardrails.scanned_today("s1") == 600


def test_redshift_queries_are_charged_the_configured_estimate() -> None:
    service = make(
        FakeEngine(dialect="redshift", scanned=None),
        limits=QueryLimits(redshift_scan_estimate_bytes=5_000),
    )
    run(service, executive("erin"))
    assert service.guardrails.scanned_today("s1") == 5_000


def test_failed_engine_queries_are_charged_what_they_scanned() -> None:
    service = make(FakeEngine(fail=EngineError("boom", scanned_bytes=300)))
    with pytest.raises(EngineError, match="boom"):
        run(service, executive("erin"))
    assert service.guardrails.scanned_today("s1") == 300


def test_concurrency_limit_per_site_refuses_when_no_slot_frees_up() -> None:
    engine = FakeEngine()
    engine.gate = threading.Event()
    service = make(engine, site_limits=SiteLimits(None, 1, 0.05))
    worker = threading.Thread(target=run, args=(service, manager("alice")))
    worker.start()
    assert engine.started.wait(5)
    try:
        with pytest.raises(GuardrailError) as refused:
            run(service, manager("bob"))
        assert refused.value.code == "too_many_queries"
    finally:
        engine.gate.set()
        worker.join(5)
    engine.gate = None
    run(service, manager("bob"))  # the slot is free again


# ---- audit and explore validation ---------------------------------------------------------------


def test_audit_line_records_cache_bytes_and_duration_but_no_values(caplog: pytest.LogCaptureFixture) -> None:
    service = make(FakeEngine(scanned=4_096))
    with caplog.at_level(logging.INFO, logger="tabfree.audit"):
        run(service, manager("alice"))
        run(service, manager("alice"))
    miss, hit = (r.getMessage() for r in caplog.records)
    assert miss.startswith("query user=alice dashboard=d query:by_region rows=2 truncated=False cache=miss")
    assert "scanned_bytes=4096" in miss
    assert "duration_ms=" in miss
    assert "cache=hit scanned_bytes=0" in hit
    assert "East" not in miss


def test_explore_queries_are_checked_against_the_schema() -> None:
    service = make(FakeEngine())
    erin = executive("erin")
    with pytest.raises(CompileError, match="invalid query"):
        service.explore("superstore_sales", {}, erin)  # needs dimensions or measures
    with pytest.raises(CompileError, match="invalid query"):
        service.explore("superstore_sales", {"dimensions": ["Region"], "limit": 0}, erin)
    with pytest.raises(CompileError, match="from the URL"):
        service.explore("superstore_sales", {"model": "x", "dimensions": ["Region"]}, erin)
    with pytest.raises(CompileError, match="unknown model"):
        service.explore("nope", {"dimensions": ["Region"]}, erin)
    assert query_errors({"measures": ["Sales"], "where": [], "sort": [{"by": "Sales", "dir": "desc"}]}) == []
    assert any("bogus" in e for e in query_errors({"measures": ["Sales"], "bogus": 1}))
