"""Retry with exponential backoff for throttled (429) and transient (5xx) Tableau responses."""

from __future__ import annotations

import time
from collections.abc import Callable


def status_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status from a TSC (or requests) exception; None when unknown.

    TSC's ``ServerResponseError.code`` is a REST error code such as ``"429000"`` or ``"500001"``
    whose first three digits are the HTTP status; ``InternalServerError.code`` is the status itself.
    """
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if value is None:
            continue
        text = str(value)
        if len(text) >= 3 and text[:3].isdigit():
            return int(text[:3])
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_transient(exc: BaseException) -> bool:
    status = status_of(exc)
    return status is not None and (status == 429 or 500 <= status < 600)


def with_retries[T](
    call: Callable[[], T],
    attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    retryable: Callable[[BaseException], bool] = is_transient,
) -> T:
    """Call ``call()``; on a retryable error wait ``base_delay * 2**n`` (capped) and try again."""
    for attempt in range(attempts - 1):
        try:
            return call()
        except Exception as exc:
            if not retryable(exc):
                raise
            sleep(min(max_delay, base_delay * 2**attempt))
    return call()
