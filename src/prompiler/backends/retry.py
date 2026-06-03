"""Retry helper — transient-error exponential backoff for backend adapters.

Wraps a zero-arg async ``operation`` and retries on transient HTTP failures:

- ``httpx.HTTPStatusError`` whose ``response.status_code`` is in the policy's
  ``retriable_status`` set (default: 429, 500, 502, 503, 504).
- ``httpx.TransportError`` subclasses (connect/read/protocol errors).

Backoff schedule is exponential: ``base_delay * 2 ** (attempt - 1)`` seconds
between attempts. With PRD §7.2 defaults (``max_attempts=3``, ``base_delay=1.0``)
the sleeps are ``[1.0, 2.0]`` — three total attempts, two waits.

``sleep`` is injectable so unit tests can capture the schedule without elapsed
wall time. Adapters compose by handing ``with_retry`` a closure that performs
the HTTP request plus ``raise_for_status()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

import httpx

T = TypeVar("T")

DEFAULT_RETRIABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """Transient-error retry budget for a single backend call.

    ``max_attempts`` counts the initial try; ``max_attempts=1`` disables retry.
    ``base_delay`` is the first sleep in seconds; subsequent sleeps double.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    retriable_status: frozenset[int] = field(default_factory=lambda: DEFAULT_RETRIABLE_STATUS)


def _is_transient(exc: BaseException, retriable_status: frozenset[int]) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in retriable_status
    return isinstance(exc, httpx.TransportError)


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Invoke ``operation``, retrying on transient errors per ``policy``.

    Non-transient exceptions propagate immediately. Transient exceptions on
    the final attempt also propagate — callers see the underlying ``httpx``
    error, not a wrapper.

    ``sleep`` defaults to ``asyncio.sleep`` resolved at call time so tests can
    monkeypatch ``prompiler.backends.retry.asyncio.sleep`` without re-binding
    captured defaults.
    """
    if policy.max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {policy.max_attempts}")
    _sleep = sleep if sleep is not None else asyncio.sleep
    attempt = 0
    while True:
        try:
            return await operation()
        except Exception as exc:
            attempt += 1
            if attempt >= policy.max_attempts or not _is_transient(exc, policy.retriable_status):
                raise
            delay = policy.base_delay * (2 ** (attempt - 1))
            await _sleep(delay)
