"""Shared contract test for the streaming surface of BackendAdapter.

Source of truth: docs/PLAN.md §2.4 Track G1 — "the Protocol defines the
streaming signature; ``supports('streaming')`` is queryable; a non-streaming
adapter cleanly reports unsupported." This file is the RED test for that
contract; the buffered ``extract`` contract lives in
``tests/test_backend_contract.py`` and is untouched here.

The streaming contract adds two symbols to ``prompiler.backends.base``:

  * ``StreamEvent`` — one incremental delta, with a terminal event carrying the
    final ``ExtractResult`` (``result is not None`` ⇔ terminal).
  * ``StreamingBackendAdapter`` — a ``@runtime_checkable`` Protocol that extends
    ``BackendAdapter`` with ``stream_extract``. Adapters opt in individually:
    Claude, OpenAI, and Gemini stream in G2 and Ollama in G3 (all satisfy
    ``StreamingBackendAdapter`` and report ``supports('streaming') is True``),
    while ``MockAdapter`` remains buffered-only — it keeps satisfying
    ``BackendAdapter`` and reports ``supports('streaming') is False`` via the
    unknown-feature default.

No network: the real-adapter checks build a bare ``httpx.AsyncClient`` and only
exercise ``isinstance`` / ``supports`` (no request is issued), closing the
client in a ``finally``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest

from prompiler.backends import (
    BackendAdapter,
    ClaudeAdapter,
    ExtractResult,
    GeminiAdapter,
    ModalContent,
    OllamaAdapter,
    OpenAIAdapter,
    StreamEvent,
    StreamingBackendAdapter,
)
from prompiler.backends.mock import MockAdapter

STREAMING_ADAPTER_CLASSES = [ClaudeAdapter, OpenAIAdapter, GeminiAdapter, OllamaAdapter]
NON_STREAMING_ADAPTER_CLASSES = [MockAdapter]


def _terminal_result() -> ExtractResult:
    return ExtractResult(
        data={"label": "a", "reason": "b"},
        system_fingerprint=None,
        deterministic=True,
    )


class _FakeStreamingAdapter:
    """In-memory adapter that satisfies both Protocols, for contract assertions."""

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> ExtractResult:
        return _terminal_result()

    def supports(self, feature: str) -> bool:
        return feature == "streaming"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)

    async def stream_extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(delta='{"label":')
        yield StreamEvent(delta='"a","reason":"b"}')
        yield StreamEvent(delta="", result=_terminal_result())


@pytest.mark.unit
def test_streaming_adapter_satisfies_both_protocols() -> None:
    adapter = _FakeStreamingAdapter()
    assert isinstance(adapter, BackendAdapter)
    assert isinstance(adapter, StreamingBackendAdapter)


@pytest.mark.unit
@pytest.mark.parametrize("cls", NON_STREAMING_ADAPTER_CLASSES)
def test_non_streaming_adapter_classes_have_no_streaming_method(cls: type) -> None:
    assert not hasattr(cls, "stream_extract")


@pytest.mark.unit
@pytest.mark.parametrize("cls", STREAMING_ADAPTER_CLASSES)
def test_streaming_adapter_classes_have_streaming_method(cls: type) -> None:
    assert hasattr(cls, "stream_extract")


@pytest.mark.unit
def test_mock_adapter_reports_streaming_unsupported() -> None:
    assert MockAdapter().supports("streaming") is False


@pytest.mark.unit
@pytest.mark.parametrize("cls", STREAMING_ADAPTER_CLASSES)
def test_streaming_real_adapter_reports_streaming_supported(cls: type) -> None:
    client = httpx.AsyncClient()
    try:
        adapter = cls(client=client)
        assert isinstance(adapter, BackendAdapter)
        assert isinstance(adapter, StreamingBackendAdapter)
        assert adapter.supports("streaming") is True
    finally:
        asyncio.run(client.aclose())


@pytest.mark.unit
def test_stream_event_non_terminal_carries_no_result() -> None:
    event = StreamEvent(delta='{"label":')
    assert event.result is None
    assert event.is_terminal is False


@pytest.mark.unit
def test_stream_event_terminal_carries_result() -> None:
    result = _terminal_result()
    event = StreamEvent(delta="", result=result)
    assert event.result is result
    assert event.is_terminal is True


@pytest.mark.unit
def test_stream_extract_required_params_by_signature() -> None:
    params = inspect.signature(_FakeStreamingAdapter.stream_extract).parameters
    assert "prompt" in params
    assert "json_schema" in params


@pytest.mark.unit
def test_stream_extract_yields_events_with_terminal_result() -> None:
    adapter = _FakeStreamingAdapter()

    async def _drain() -> list[StreamEvent]:
        return [
            event
            async for event in adapter.stream_extract(
                prompt="x", json_schema={"properties": {}, "required": []}
            )
        ]

    events = asyncio.run(_drain())
    assert len(events) == 3
    assert [e.is_terminal for e in events] == [False, False, True]
    assert events[-1].result is not None
    assert isinstance(events[-1].result, ExtractResult)
    assert all(e.result is None for e in events[:-1])
