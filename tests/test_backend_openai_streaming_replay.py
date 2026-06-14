"""Replay test for OpenAI SSE streaming (Q4 Track G2 fan-out — RED).

Source of truth: docs/PLAN.md §2.4 Track G2 — "SSE streaming adapters (Claude,
OpenAI, Gemini)". This file is the RED replay test for the OpenAI fan-out: it
drives ``OpenAIAdapter.stream_extract`` over a recorded ``text/event-stream``
cassette and asserts the streaming contract end-to-end.

It is the OpenAI sibling of ``tests/test_backend_streaming_replay.py`` (Claude).
The cassette ``tests/cassettes/openai_streaming.json`` is a single POST to
``/v1/chat/completions`` whose response body is an OpenAI ``chat.completion.chunk``
SSE wire log: an opening tool-call chunk, three ``function.arguments`` fragments,
a ``finish_reason: tool_calls`` chunk, a usage-only chunk, then ``data: [DONE]``.
Its three argument fragments concatenate to the same extract result the buffered
``openai_happy_path.json`` cassette returns, so streaming and buffered agree on
the terminal payload.

No network: playback is over ``make_cassette_transport`` (method+URL match only,
per LL-004). The error-visibility test uses an inline 500 ``MockTransport`` (no
cassette file) to assert the raised ``HTTPStatusError`` carries the vendor label
and the upstream body (LL-003).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from _cassette import CassettePlayer
from _cassette_transport import make_cassette_transport
from prompiler.backends import ExtractResult, OpenAIAdapter, StreamEvent
from prompiler.backends.openai import OPENAI_BASE_URL

STREAMING_CASSETTE = Path(__file__).parent / "cassettes" / "openai_streaming.json"

STREAMING_PROMPT = "Classify the following text into one routing bucket."

STREAMING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "reason": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}

EXPECTED_TERMINAL_DATA: dict[str, str] = {
    "label": "support",
    "reason": "user requests human assistance",
}


def _streaming_factory() -> OpenAIAdapter:
    player = CassettePlayer.from_json(STREAMING_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=OPENAI_BASE_URL)
    return OpenAIAdapter(client=client)


def _drain(adapter: OpenAIAdapter) -> list[StreamEvent]:
    async def _run() -> list[StreamEvent]:
        return [
            event
            async for event in adapter.stream_extract(
                prompt=STREAMING_PROMPT, json_schema=STREAMING_SCHEMA
            )
        ]

    return asyncio.run(_run())


@pytest.mark.unit
def test_openai_stream_extract_yields_deltas_then_terminal() -> None:
    events = _drain(_streaming_factory())
    assert [e.is_terminal for e in events] == [False, False, False, True]
    assert all(e.result is None for e in events[:-1])
    terminal = events[-1]
    assert isinstance(terminal.result, ExtractResult)
    assert terminal.result.data == EXPECTED_TERMINAL_DATA
    assert terminal.result.deterministic is True


@pytest.mark.unit
def test_openai_stream_delta_concatenation_parses_to_terminal() -> None:
    events = _drain(_streaming_factory())
    assembled = "".join(e.delta for e in events)
    assert json.loads(assembled) == EXPECTED_TERMINAL_DATA
    assert events[-1].result is not None
    assert json.loads(assembled) == events[-1].result.data


@pytest.mark.unit
def test_openai_stream_extract_matches_buffered_terminal_payload() -> None:
    # Streaming and buffered cassettes are recorded from the same logical
    # response, so the streamed terminal payload must field-equal the buffered
    # extract result asserted in tests/test_backend_contract.py.
    events = _drain(_streaming_factory())
    assert events[-1].result is not None
    assert events[-1].result.data == EXPECTED_TERMINAL_DATA


@pytest.mark.unit
def test_openai_stream_extract_surfaces_http_error_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream stream boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url=OPENAI_BASE_URL)
    adapter = OpenAIAdapter(client=client)

    async def _run() -> None:
        async for _event in adapter.stream_extract(
            prompt=STREAMING_PROMPT, json_schema=STREAMING_SCHEMA
        ):
            pass

    try:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            asyncio.run(_run())
    finally:
        asyncio.run(client.aclose())

    message = str(excinfo.value)
    assert "OpenAI" in message
    assert "500" in message
    assert "upstream stream boom" in message


@pytest.mark.unit
def test_openai_stream_extract_raises_runtime_error_on_malformed_json() -> None:
    # Parity with the buffered path (LL-003): when the assembled tool-call
    # arguments fragments do not form valid JSON, stream_extract must raise a
    # vendor-prefixed RuntimeError, not leak a raw json.JSONDecodeError.
    chunk = {
        "choices": [{"delta": {"tool_calls": [{"function": {"arguments": "{not valid json"}}]}}]
    }
    body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url=OPENAI_BASE_URL)
    adapter = OpenAIAdapter(client=client)

    async def _run() -> None:
        async for _event in adapter.stream_extract(
            prompt=STREAMING_PROMPT, json_schema=STREAMING_SCHEMA
        ):
            pass

    try:
        with pytest.raises(RuntimeError) as excinfo:
            asyncio.run(_run())
    finally:
        asyncio.run(client.aclose())

    assert "OpenAI" in str(excinfo.value)
