"""The query result cache (ADR-007: "caching is mandatory at scale").

A result is cached under :func:`cache_key`: a SHA-256 over the compiled SQL, every bound parameter
value (row-level security and user functions compile to bind parameters, so the viewer's RLS context
is part of the key), the model, its connection, its data version and the date relative dates anchor
on. Two viewers share an entry only when the engine would receive exactly the same SQL and values,
which is precisely when it would return the same rows. A new data version (extract refresh, P6.2)
changes every key of that model, so stale entries are never read again and age out.

The backend is pluggable (:class:`ResultCache`); :class:`MemoryResultCache` is an in-process LRU with
a TTL. A shared backend (Valkey/ElastiCache) can implement the same two methods later.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from tabfree_compiler import CompiledQuery

Clock = Callable[[], float]

#: Bump when the cached value's shape or the key's inputs change.
KEY_VERSION = "tabfree-result-cache/1"


@dataclass(frozen=True)
class CachedResult:
    #: Rows already capped and converted to JSON values.
    rows: list[list[Any]]
    truncated: bool
    truncated_by: str | None
    #: Bytes the original engine query scanned (reported, not charged again).
    scanned_bytes: int


class ResultCache(Protocol):
    def get(self, key: str) -> CachedResult | None: ...

    def put(self, key: str, value: CachedResult) -> None: ...


def _canonical(value: Any) -> Any:
    """Bind values as JSON, keeping types apart (1, 1.0, True and "1" must not collide)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return {"bool": value}
    if isinstance(value, int):
        return {"int": str(value)}
    if isinstance(value, float):
        return {"float": repr(value)}
    if isinstance(value, dt.datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, dt.date):
        return {"date": value.isoformat()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return {type(value).__name__: str(value)}


def cache_key(
    compiled: CompiledQuery,
    *,
    model: str,
    connection: Mapping[str, Any] | None,
    data_version: str,
    today: dt.date,
    caps: tuple[int, int],
) -> str:
    """hash(compiled SQL, bound parameter values incl. RLS context, data version) plus scope."""
    material = {
        "v": KEY_VERSION,
        "model": model,
        "connection": sorted((str(k), str(v)) for k, v in (connection or {}).items()),
        "dataVersion": data_version,
        "dialect": compiled.dialect,
        "sql": compiled.sql,
        "parameters": [_canonical(v) for v in compiled.parameters],
        "namedParameters": sorted((k, _canonical(v)) for k, v in compiled.named_parameters.items()),
        # Relative dates bind "today" as a parameter; without an anchor the engine's CURRENT_DATE is
        # used, so the date still belongs in the key.
        "today": today.isoformat(),
        "caps": list(caps),
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class MemoryResultCache:
    """In-process LRU with a time-to-live per entry. Thread-safe."""

    def __init__(self, max_entries: int = 2_000, ttl_s: float = 300.0, clock: Clock = time.time) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.max_entries, self.ttl_s, self._clock = max_entries, ttl_s, clock
        self._entries: OrderedDict[str, tuple[float, CachedResult]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> CachedResult | None:
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            expires, value = item
            if self._clock() >= expires:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: str, value: CachedResult) -> None:
        with self._lock:
            self._entries[key] = (self._clock() + self.ttl_s, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


class NoCache:
    """The cache turned off."""

    def get(self, key: str) -> CachedResult | None:
        return None

    def put(self, key: str, value: CachedResult) -> None:
        return None
