"""Reliability helpers (retry wrapper)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def run_with_retry(
    op: Callable[[], T],
    *,
    retries: int = 2,
    delay_seconds: float = 0.1,
    retry_on: tuple[type[Exception], ...] = (TimeoutError,),
) -> T:
    """Run operation with bounded retry for transient failures."""
    attempts = retries + 1
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            return op()
        except retry_on as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay_seconds)

    assert last_error is not None
    raise last_error
