"""Per-site cost guardrails: a daily scanned-bytes budget and a cap on concurrent engine queries.

Athena bills per byte scanned and allows 200 active queries per account (ADR-007), so one busy site
must not exhaust either for the others. Each site has:

* a **daily budget** of scanned bytes (UTC day). Athena reports ``DataScannedInBytes`` per query;
  Redshift does not, so each Redshift query is charged a configured estimate. Once a site has spent
  its budget, new engine queries are refused with :class:`GuardrailError` (HTTP 429,
  ``scan_budget_exceeded``) until the day rolls over. Cached results stay available: they cost nothing.
* a **concurrency limit**: a semaphore around engine execution. A query waits up to the queue timeout
  for a slot, then is refused (HTTP 429, ``too_many_queries``).

The ledger lives in process memory; with several API tasks each task enforces its own share. A shared
ledger (Postgres or DynamoDB) replaces it when the service scales out (Phase 3).
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

Clock = Callable[[], float]


class GuardrailError(RuntimeError):
    """A guardrail refused the query. ``code`` is stable; the message is safe to show."""

    def __init__(self, code: str, message: str, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.retry_after_s = code, message, retry_after_s


@dataclass(frozen=True)
class SiteLimits:
    #: Bytes a site may scan per UTC day; None means no budget.
    daily_scan_budget_bytes: int | None
    max_concurrent_queries: int
    queue_timeout_s: float


class _SiteState:
    def __init__(self, limits: SiteLimits) -> None:
        self.limits = limits
        self.slots = threading.BoundedSemaphore(limits.max_concurrent_queries)
        self.day: dt.date | None = None
        self.scanned = 0
        self.queries = 0


class Guardrails:
    def __init__(self, limits_for: Callable[[str], SiteLimits], clock: Clock = time.time) -> None:
        self._limits_for, self._clock = limits_for, clock
        self._sites: dict[str, _SiteState] = {}
        self._lock = threading.Lock()

    def _today(self) -> dt.date:
        return dt.datetime.fromtimestamp(self._clock(), dt.UTC).date()

    def _state(self, site: str) -> _SiteState:
        with self._lock:
            state = self._sites.get(site)
            if state is None:
                state = self._sites[site] = _SiteState(self._limits_for(site))
            today = self._today()
            if state.day != today:
                state.day, state.scanned, state.queries = today, 0, 0
            return state

    def scanned_today(self, site: str) -> int:
        return self._state(site).scanned

    def check_budget(self, site: str) -> None:
        state = self._state(site)
        budget = state.limits.daily_scan_budget_bytes
        if budget is not None and state.scanned >= budget:
            now = dt.datetime.fromtimestamp(self._clock(), dt.UTC)
            midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), dt.UTC)
            raise GuardrailError(
                "scan_budget_exceeded",
                f"this site has used its daily query budget ({_size(state.scanned)} of {_size(budget)} "
                "scanned); cached results are still available, new queries resume at 00:00 UTC",
                retry_after_s=max(1, int((midnight - now).total_seconds())),
            )

    def charge(self, site: str, scanned_bytes: int) -> None:
        state = self._state(site)
        with self._lock:
            state.scanned += max(0, scanned_bytes)
            state.queries += 1

    @contextmanager
    def slot(self, site: str) -> Iterator[None]:
        state = self._state(site)
        if not state.slots.acquire(timeout=state.limits.queue_timeout_s):
            raise GuardrailError(
                "too_many_queries",
                f"this site is already running {state.limits.max_concurrent_queries} queries; try again",
                retry_after_s=1,
            )
        try:
            yield
        finally:
            state.slots.release()


def _size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"  # pragma: no cover
