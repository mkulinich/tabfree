"""Guardrails on AI use (BUILD_PLAN P4.6): rate limits on tool calls and daily token budgets.

- **Rate limits:** each user may make a bounded number of MCP tool calls per minute (a token bucket per
  user, per process), so a runaway agent loop cannot hammer the query engines.
- **Token budgets:** model tokens per user and per site per UTC day. The quick-edit loop and the
  sandbox report usage after every model call; a new call is refused once a budget is spent. Usage is
  kept in the ``ai_usage`` table when there is a database (shared by every API task), in memory
  otherwise. Site settings ``aiDailyTokensPerUser`` and ``aiDailyTokensPerSite`` override the defaults.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, func, insert, select, update

from tabfree_api.db import schema as t


class BudgetError(RuntimeError):
    """Refused by a guardrail; ``code`` is stable and the message is safe to show."""

    def __init__(self, code: str, message: str, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.retry_after_s = code, message, retry_after_s


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> None:
        if self.per_minute <= 0:
            return
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.per_minute), now))
            tokens = min(float(self.per_minute), tokens + (now - last) * self.per_minute / 60.0)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                wait = int((1.0 - tokens) * 60.0 / self.per_minute) + 1
                raise BudgetError("rate_limited", f"too many tool calls; wait {wait} s", wait)
            self._buckets[key] = (tokens - 1.0, now)


def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class TokenBudget:
    def __init__(
        self,
        site_id: str,
        *,
        per_user: int,
        per_site: int,
        engine: Engine | None = None,
        overrides: Callable[[], dict[str, Any]] | None = None,
        today: Callable[[], str] = _today,
    ) -> None:
        self.site_id = site_id
        self.per_user, self.per_site = per_user, per_site
        self.engine = engine
        self._overrides = overrides or (lambda: {})
        self._today = today
        self._memory: dict[tuple[str, str], list[int]] = {}
        self._lock = threading.Lock()

    def limits(self) -> tuple[int, int]:
        settings = self._overrides()
        user = settings.get("aiDailyTokensPerUser", self.per_user)
        site = settings.get("aiDailyTokensPerSite", self.per_site)
        return int(user), int(site)

    def used(self, username: str | None = None) -> int:
        day = self._today()
        if self.engine is None:
            with self._lock:
                rows = [
                    v
                    for (u, d), v in self._memory.items()
                    if d == day and (username is None or u == username)
                ]
            return sum(r[1] + r[2] for r in rows)
        u = t.ai_usage
        query = select(func.coalesce(func.sum(u.c.input_tokens + u.c.output_tokens), 0)).where(
            u.c.site_id == self.site_id, u.c.day == day
        )
        if username is not None:
            query = query.where(u.c.username == username)
        with self.engine.connect() as conn:
            return int(conn.execute(query).scalar_one())

    def check(self, username: str) -> None:
        per_user, per_site = self.limits()
        if per_user and self.used(username) >= per_user:
            raise BudgetError(
                "ai_budget_exceeded", "you have used today's AI budget; it resets at 00:00 UTC", 3600
            )
        if per_site and self.used() >= per_site:
            raise BudgetError(
                "ai_budget_exceeded", "this site has used today's AI budget; it resets at 00:00 UTC", 3600
            )

    def record(self, username: str, input_tokens: int, output_tokens: int) -> None:
        day = self._today()
        if self.engine is None:
            with self._lock:
                row = self._memory.setdefault((username, day), [0, 0, 0])
                row[0] += 1
                row[1] += input_tokens
                row[2] += output_tokens
            return
        u = t.ai_usage
        key = (u.c.site_id == self.site_id, u.c.username == username, u.c.day == day)
        with self.engine.begin() as conn:
            changed = conn.execute(
                update(u)
                .where(*key)
                .values(
                    requests=u.c.requests + 1,
                    input_tokens=u.c.input_tokens + input_tokens,
                    output_tokens=u.c.output_tokens + output_tokens,
                )
            ).rowcount
            if not changed:
                conn.execute(
                    insert(u).values(
                        site_id=self.site_id,
                        username=username,
                        day=day,
                        requests=1,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                )
