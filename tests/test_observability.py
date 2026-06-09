"""Unit tests for per-call observability hook (P2.obs).

Covers PRD §FR-12 observability surface for backend adapters:

- ``BackendCallMetrics`` frozen dataclass shape
- ``PricingTable.cost_usd`` math + unknown-pair fallback
- ``DEFAULT_PRICING_TABLE`` contains entries for all four shipped backends
- ``emit_call_metrics`` no-op when hook is ``None``; computes cost + dispatches otherwise
- Each adapter (Claude / OpenAI / Gemini / Ollama) emits a single
  ``BackendCallMetrics`` after a successful ``extract`` call, populated from
  the vendor-specific token-usage JSON path; hook is awaited
- Adapter does not crash when ``observability`` ctor arg is ``None``
- Missing token-usage fields fall through as 0
- Non-2xx responses (non-retriable) do not emit (failure path)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from typing import Any

import httpx
import pytest

from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.gemini import GeminiAdapter
from prompiler.backends.observability import (
    DEFAULT_PRICING_TABLE,
    BackendCallMetrics,
    FanOutHook,
    PricingEntry,
    PricingTable,
    emit_call_metrics,
)
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter
from prompiler.backends.retry import RetryPolicy


class _RecordingHook:
    """ObservabilityHook test double — records every metrics object received."""

    def __init__(self) -> None:
        self.calls: list[BackendCallMetrics] = []

    async def on_call(self, metrics: BackendCallMetrics) -> None:
        self.calls.append(metrics)


@pytest.mark.unit
def test_backend_call_metrics_is_frozen() -> None:
    m = BackendCallMetrics(
        backend="claude",
        model="claude-haiku-4-5-20251001",
        latency_seconds=0.123,
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.001,
    )
    with pytest.raises(FrozenInstanceError):
        m.cost_usd = 0.0  # type: ignore[misc]


@pytest.mark.unit
def test_pricing_table_cost_usd_math() -> None:
    table = PricingTable(
        rates={("openai", "gpt-4o-mini"): PricingEntry(prompt_per_1m=0.15, completion_per_1m=0.60)}
    )
    # 1_000_000 prompt tokens * 0.15 / 1_000_000 = 0.15
    # 500_000 completion tokens * 0.60 / 1_000_000 = 0.30
    cost = table.cost_usd(
        backend="openai",
        model="gpt-4o-mini",
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
    )
    assert cost == pytest.approx(0.45)


@pytest.mark.unit
def test_pricing_table_unknown_pair_returns_zero() -> None:
    table = PricingTable(rates={})
    assert (
        table.cost_usd(
            backend="anything",
            model="any-model",
            prompt_tokens=10,
            completion_tokens=10,
        )
        == 0.0
    )


@pytest.mark.unit
def test_default_pricing_table_covers_shipped_backends() -> None:
    expected_keys = {
        ("claude", "claude-haiku-4-5-20251001"),
        ("openai", "gpt-4o-mini"),
        ("gemini", "gemini-2.5-flash"),
        ("ollama", "llama3.1"),
    }
    assert expected_keys.issubset(set(DEFAULT_PRICING_TABLE.rates.keys()))


@pytest.mark.unit
def test_emit_call_metrics_no_op_when_hook_none() -> None:
    # Must not raise; just returns.
    asyncio.run(
        emit_call_metrics(
            hook=None,
            backend="openai",
            model="gpt-4o-mini",
            latency_seconds=0.1,
            prompt_tokens=10,
            completion_tokens=5,
            pricing=DEFAULT_PRICING_TABLE,
        )
    )


@pytest.mark.unit
def test_emit_call_metrics_dispatches_with_cost() -> None:
    hook = _RecordingHook()
    asyncio.run(
        emit_call_metrics(
            hook=hook,
            backend="openai",
            model="gpt-4o-mini",
            latency_seconds=0.25,
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            pricing=DEFAULT_PRICING_TABLE,
        )
    )
    assert len(hook.calls) == 1
    m = hook.calls[0]
    assert m.backend == "openai"
    assert m.model == "gpt-4o-mini"
    assert m.latency_seconds == 0.25
    assert m.prompt_tokens == 1_000_000
    assert m.completion_tokens == 500_000
    assert m.cost_usd == pytest.approx(0.45)


# --- Adapter integration -----------------------------------------------------

JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
}


def _ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=json.dumps(body).encode(),
    )


def _claude_body(prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    return {
        "content": [
            {"type": "tool_use", "name": "extract", "input": {"label": "billing"}},
        ],
        "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens},
    }


def _openai_body(prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "extract",
                                "arguments": json.dumps({"label": "billing"}),
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def _gemini_body(prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "extract",
                                "args": {"label": "billing"},
                            }
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens,
        },
    }


def _ollama_body(prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    return {
        "message": {"content": json.dumps({"label": "billing"})},
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
    }


@pytest.mark.unit
def test_claude_adapter_emits_metrics_on_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_claude_body(prompt_tokens=100, completion_tokens=50))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.anthropic.com",
        headers={"x-api-key": "test", "anthropic-version": "2023-06-01"},
    )
    hook = _RecordingHook()
    adapter = ClaudeAdapter(
        client=client,
        observability=hook,
        pricing=DEFAULT_PRICING_TABLE,
    )

    result = asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())

    assert result.data == {"label": "billing"}
    assert len(hook.calls) == 1
    m = hook.calls[0]
    assert m.backend == "claude"
    assert m.model == "claude-haiku-4-5-20251001"
    assert m.prompt_tokens == 100
    assert m.completion_tokens == 50
    assert m.latency_seconds >= 0.0
    # claude-haiku-4-5 pricing $1.00 / $5.00 per 1M
    expected_cost = 100 * 1.0 / 1_000_000 + 50 * 5.0 / 1_000_000
    assert m.cost_usd == pytest.approx(expected_cost)


@pytest.mark.unit
def test_openai_adapter_emits_metrics_on_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_openai_body(prompt_tokens=80, completion_tokens=40))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
        headers={"authorization": "Bearer test"},
    )
    hook = _RecordingHook()
    adapter = OpenAIAdapter(
        client=client,
        observability=hook,
        pricing=DEFAULT_PRICING_TABLE,
    )

    result = asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())

    assert result.data == {"label": "billing"}
    assert len(hook.calls) == 1
    m = hook.calls[0]
    assert m.backend == "openai"
    assert m.model == "gpt-4o-mini"
    assert m.prompt_tokens == 80
    assert m.completion_tokens == 40
    expected_cost = 80 * 0.15 / 1_000_000 + 40 * 0.60 / 1_000_000
    assert m.cost_usd == pytest.approx(expected_cost)


@pytest.mark.unit
def test_gemini_adapter_emits_metrics_on_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_gemini_body(prompt_tokens=60, completion_tokens=30))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://generativelanguage.googleapis.com",
        headers={"x-goog-api-key": "test"},
    )
    hook = _RecordingHook()
    adapter = GeminiAdapter(
        client=client,
        observability=hook,
        pricing=DEFAULT_PRICING_TABLE,
    )

    result = asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())

    assert result.data == {"label": "billing"}
    assert len(hook.calls) == 1
    m = hook.calls[0]
    assert m.backend == "gemini"
    assert m.model == "gemini-2.5-flash"
    assert m.prompt_tokens == 60
    assert m.completion_tokens == 30
    expected_cost = 60 * 0.30 / 1_000_000 + 30 * 2.50 / 1_000_000
    assert m.cost_usd == pytest.approx(expected_cost)


@pytest.mark.unit
def test_ollama_adapter_emits_metrics_on_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_ollama_body(prompt_tokens=40, completion_tokens=20))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:11434",
    )
    hook = _RecordingHook()
    adapter = OllamaAdapter(
        client=client,
        observability=hook,
        pricing=DEFAULT_PRICING_TABLE,
    )

    result = asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())

    assert result.data == {"label": "billing"}
    assert len(hook.calls) == 1
    m = hook.calls[0]
    assert m.backend == "ollama"
    assert m.model == "llama3.1"
    assert m.prompt_tokens == 40
    assert m.completion_tokens == 20
    # Local Ollama priced at 0.0 in DEFAULT_PRICING_TABLE
    assert m.cost_usd == 0.0


@pytest.mark.unit
def test_adapter_does_not_crash_without_observability() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_openai_body(prompt_tokens=10, completion_tokens=5))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
        headers={"authorization": "Bearer test"},
    )
    adapter = OpenAIAdapter(client=client)  # no observability kwarg
    result = asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())
    assert result.data == {"label": "billing"}


@pytest.mark.unit
def test_adapter_emits_zero_tokens_when_usage_missing() -> None:
    body_without_usage: dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "extract",
                                "arguments": json.dumps({"label": "billing"}),
                            }
                        }
                    ]
                }
            }
        ],
        # no "usage" key at all
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(body_without_usage)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
        headers={"authorization": "Bearer test"},
    )
    hook = _RecordingHook()
    adapter = OpenAIAdapter(client=client, observability=hook)
    asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())

    assert len(hook.calls) == 1
    m = hook.calls[0]
    assert m.prompt_tokens == 0
    assert m.completion_tokens == 0
    assert m.cost_usd == 0.0


@pytest.mark.unit
def test_adapter_does_not_emit_on_non_retriable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    async def _instant_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("prompiler.backends.retry.asyncio.sleep", _instant_sleep)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
        headers={"authorization": "Bearer test"},
    )
    hook = _RecordingHook()
    adapter = OpenAIAdapter(
        client=client,
        observability=hook,
        retry_policy=RetryPolicy(max_attempts=1),
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(adapter.extract(prompt="hi", json_schema=JSON_SCHEMA))
    asyncio.run(adapter.aclose())

    assert hook.calls == []


def _metrics(**over: object) -> BackendCallMetrics:
    base: dict[str, object] = {
        "backend": "claude",
        "model": "claude-haiku-4-5-20251001",
        "latency_seconds": 0.1,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cost_usd": 0.001,
    }
    base.update(over)
    return BackendCallMetrics(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_fan_out_hook_forwards_to_all_children() -> None:
    h1 = _RecordingHook()
    h2 = _RecordingHook()
    fan = FanOutHook([h1, h2])
    m = _metrics()

    asyncio.run(fan.on_call(m))

    assert h1.calls == [m]
    assert h2.calls == [m]


@pytest.mark.unit
def test_fan_out_hook_preserves_order() -> None:
    order: list[str] = []

    class _Marker:
        def __init__(self, tag: str) -> None:
            self._tag = tag

        async def on_call(self, _metrics: BackendCallMetrics) -> None:
            order.append(self._tag)

    fan = FanOutHook([_Marker("a"), _Marker("b"), _Marker("c")])
    asyncio.run(fan.on_call(_metrics()))

    assert order == ["a", "b", "c"]


@pytest.mark.unit
def test_fan_out_hook_empty_is_noop() -> None:
    fan = FanOutHook([])
    asyncio.run(fan.on_call(_metrics()))
