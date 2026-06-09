"""Unit tests for retry helper and adapter wiring (P2.retry).

Covers PRD §7.2: "One retry on transient backend error (429, 5xx) with
exponential backoff (1s, 2s, 4s; max 3 attempts total)."

Sub-steps:
- B: ``_is_transient`` classifier — status codes + transport errors
- C: ``with_retry`` backoff schedule — capturing sleep, default policy
- D: ``ClaudeAdapter`` end-to-end via ``httpx.MockTransport`` ``[429, 429, 200]``
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.retry import (
    DEFAULT_RETRIABLE_STATUS,
    RetryPolicy,
    _is_transient,
    with_retry,
)


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/x")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


@pytest.mark.unit
@pytest.mark.parametrize("status", sorted(DEFAULT_RETRIABLE_STATUS))
def test_is_transient_true_for_retriable_status(status: int) -> None:
    assert _is_transient(_status_error(status), DEFAULT_RETRIABLE_STATUS) is True


@pytest.mark.unit
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_is_transient_false_for_non_retriable_status(status: int) -> None:
    assert _is_transient(_status_error(status), DEFAULT_RETRIABLE_STATUS) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("boom"),
        httpx.ReadError("boom"),
    ],
)
def test_is_transient_true_for_transport_errors(exc: BaseException) -> None:
    assert _is_transient(exc, DEFAULT_RETRIABLE_STATUS) is True


@pytest.mark.unit
def test_is_transient_false_for_generic_exception() -> None:
    assert _is_transient(ValueError("nope"), DEFAULT_RETRIABLE_STATUS) is False


def _capturing_sleep(
    captured: list[float],
) -> Callable[[float], Awaitable[None]]:
    async def _sleep(delay: float) -> None:
        captured.append(delay)

    return _sleep


@pytest.mark.unit
def test_with_retry_returns_first_success_without_sleeping() -> None:
    calls = {"n": 0}
    captured: list[float] = []

    async def op() -> str:
        calls["n"] += 1
        return "ok"

    result = asyncio.run(with_retry(op, policy=RetryPolicy(), sleep=_capturing_sleep(captured)))

    assert result == "ok"
    assert calls["n"] == 1
    assert captured == []


@pytest.mark.unit
def test_with_retry_backoff_schedule_default_policy() -> None:
    calls = {"n": 0}
    captured: list[float] = []

    async def op() -> str:
        calls["n"] += 1
        raise _status_error(429)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(with_retry(op, policy=RetryPolicy(), sleep=_capturing_sleep(captured)))

    assert calls["n"] == 3
    assert captured == [1.0, 2.0]


@pytest.mark.unit
def test_with_retry_stops_on_non_transient() -> None:
    calls = {"n": 0}
    captured: list[float] = []

    async def op() -> str:
        calls["n"] += 1
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(with_retry(op, policy=RetryPolicy(), sleep=_capturing_sleep(captured)))

    assert calls["n"] == 1
    assert captured == []


@pytest.mark.unit
def test_with_retry_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}
    captured: list[float] = []

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(503)
        return "ok"

    result = asyncio.run(with_retry(op, policy=RetryPolicy(), sleep=_capturing_sleep(captured)))

    assert result == "ok"
    assert calls["n"] == 3
    assert captured == [1.0, 2.0]


@pytest.mark.unit
def test_with_retry_rejects_zero_attempts() -> None:
    async def op() -> str:
        return "ok"

    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        asyncio.run(with_retry(op, policy=RetryPolicy(max_attempts=0)))


def _claude_tool_use_body() -> dict[str, object]:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "extract",
                "input": {"label": "billing", "reason": "invoice mention"},
            }
        ]
    }


@pytest.mark.unit
def test_claude_adapter_retries_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(_claude_tool_use_body()).encode(),
        ),
    ]
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        idx = calls["n"]
        calls["n"] += 1
        return responses[idx]

    captured: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr("prompiler.backends.retry.asyncio.sleep", _instant_sleep)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="https://api.anthropic.com",
        headers={"x-api-key": "test", "anthropic-version": "2023-06-01"},
    )
    adapter = ClaudeAdapter(
        client=client,
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.5),
    )

    result = asyncio.run(
        adapter.extract(
            prompt="hi",
            json_schema={"type": "object", "properties": {"label": {"type": "string"}}},
        )
    )

    assert result.data == {"label": "billing", "reason": "invoice mention"}
    assert calls["n"] == 3
    assert captured == [0.5, 1.0]

    asyncio.run(adapter.aclose())
