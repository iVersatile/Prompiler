"""Replay test for Claude SSE streaming (Q4 Track G2 spike — RED).

Source of truth: docs/PLAN.md §2.4 Track G2 — "SSE streaming adapters (Claude,
OpenAI, Gemini)". This file is the RED replay test for the Claude spike: it
drives ``ClaudeAdapter.stream_extract`` over a recorded ``text/event-stream``
cassette and asserts the streaming contract end-to-end.

It is the streaming sibling of ``tests/test_backend_contract.py`` (which covers
buffered ``extract``). The cassette ``tests/cassettes/claude_streaming.json`` is
a single POST to ``/v1/messages`` whose response body is the full Anthropic SSE
wire log (``message_start`` -> ``content_block_delta`` x3 -> ``message_stop``). Its
three ``input_json_delta`` fragments concatenate to the same extract result the
buffered ``claude_happy_path.json`` cassette returns, so streaming and buffered
agree on the terminal payload.

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
from prompiler.backends import ClaudeAdapter, ExtractResult, StreamEvent
from prompiler.backends.claude import ANTHROPIC_BASE_URL

STREAMING_CASSETTE = Path(__file__).parent / "cassettes" / "claude_streaming.json"

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


def _streaming_factory() -> ClaudeAdapter:
    player = CassettePlayer.from_json(STREAMING_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=ANTHROPIC_BASE_URL)
    return ClaudeAdapter(client=client)


def _drain(adapter: ClaudeAdapter) -> list[StreamEvent]:
    async def _run() -> list[StreamEvent]:
        return [
            event
            async for event in adapter.stream_extract(
                prompt=STREAMING_PROMPT, json_schema=STREAMING_SCHEMA
            )
        ]

    return asyncio.run(_run())


@pytest.mark.unit
def test_claude_stream_extract_yields_deltas_then_terminal() -> None:
    events = _drain(_streaming_factory())
    assert [e.is_terminal for e in events] == [False, False, False, True]
    assert all(e.result is None for e in events[:-1])
    terminal = events[-1]
    assert isinstance(terminal.result, ExtractResult)
    assert terminal.result.data == EXPECTED_TERMINAL_DATA
    assert terminal.result.deterministic is False


@pytest.mark.unit
def test_claude_stream_delta_concatenation_parses_to_terminal() -> None:
    events = _drain(_streaming_factory())
    assembled = "".join(e.delta for e in events)
    assert json.loads(assembled) == EXPECTED_TERMINAL_DATA
    assert events[-1].result is not None
    assert json.loads(assembled) == events[-1].result.data


@pytest.mark.unit
def test_claude_stream_extract_matches_buffered_terminal_payload() -> None:
    # Streaming and buffered cassettes are recorded from the same logical
    # response, so the streamed terminal payload must field-equal the buffered
    # extract result asserted in tests/test_backend_contract.py.
    events = _drain(_streaming_factory())
    assert events[-1].result is not None
    assert events[-1].result.data == EXPECTED_TERMINAL_DATA


@pytest.mark.unit
def test_claude_stream_extract_surfaces_http_error_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream stream boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url=ANTHROPIC_BASE_URL)
    adapter = ClaudeAdapter(client=client)

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
    assert "Claude" in message
    assert "500" in message
    assert "upstream stream boom" in message
