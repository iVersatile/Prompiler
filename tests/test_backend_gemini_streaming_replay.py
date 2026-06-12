"""Replay test for Gemini SSE streaming (Q4 Track G2 fan-out — RED).

Source of truth: docs/PLAN.md §2.4 Track G2 — "SSE streaming adapters (Claude,
OpenAI, Gemini)". This file is the RED replay test for the Gemini fan-out: it
drives ``GeminiAdapter.stream_extract`` over a recorded ``text/event-stream``
cassette and asserts the streaming contract end-to-end.

It is the Gemini sibling of ``tests/test_backend_openai_streaming_replay.py``.
The cassette ``tests/cassettes/gemini_streaming.json`` is a single POST to
``/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse`` whose response
body is a Gemini ``streamGenerateContent`` SSE wire log: a chunk carrying one
``functionCall`` whose ``args`` is a complete dict, then a terminal chunk with
``finishReason: STOP`` and ``usageMetadata``. Gemini emits no ``[DONE]``
sentinel. Unlike OpenAI/Claude — whose ``arguments`` arrive as string fragments
to concatenate — Gemini's per-chunk ``args`` are whole dicts that *merge*, so the
parity assertion walks ``dict.update`` over the delta events rather than
``"".join``.

No network: playback is over ``make_cassette_transport`` (method+URL match only,
per LL-004; the cassette URL therefore carries the exact ``?alt=sse`` query the
adapter must POST to). The error-visibility test uses an inline 500
``MockTransport`` (no cassette file) to assert the raised ``HTTPStatusError``
carries the vendor label and the upstream body (LL-003).
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
from prompiler.backends import ExtractResult, GeminiAdapter, StreamEvent
from prompiler.backends.gemini import GEMINI_BASE_URL

STREAMING_CASSETTE = Path(__file__).parent / "cassettes" / "gemini_streaming.json"

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


def _streaming_factory() -> GeminiAdapter:
    player = CassettePlayer.from_json(STREAMING_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=GEMINI_BASE_URL)
    return GeminiAdapter(client=client)


def _drain(adapter: GeminiAdapter) -> list[StreamEvent]:
    async def _run() -> list[StreamEvent]:
        return [
            event
            async for event in adapter.stream_extract(
                prompt=STREAMING_PROMPT, json_schema=STREAMING_SCHEMA
            )
        ]

    return asyncio.run(_run())


@pytest.mark.unit
def test_gemini_stream_extract_yields_deltas_then_terminal() -> None:
    events = _drain(_streaming_factory())
    assert [e.is_terminal for e in events] == [False, True]
    assert all(e.result is None for e in events[:-1])
    terminal = events[-1]
    assert isinstance(terminal.result, ExtractResult)
    assert terminal.result.data == EXPECTED_TERMINAL_DATA
    assert terminal.result.deterministic is False
    assert terminal.result.system_fingerprint is None


@pytest.mark.unit
def test_gemini_stream_delta_merge_parses_to_terminal() -> None:
    # Gemini args arrive as whole dicts per chunk, so the streamed payload is
    # reassembled by merging the delta dicts — not by concatenating fragments.
    events = _drain(_streaming_factory())
    merged: dict[str, Any] = {}
    for event in events[:-1]:
        merged.update(json.loads(event.delta))
    assert merged == EXPECTED_TERMINAL_DATA
    assert events[-1].result is not None
    assert merged == events[-1].result.data


@pytest.mark.unit
def test_gemini_stream_extract_matches_buffered_terminal_payload() -> None:
    # Streaming and buffered cassettes are recorded from the same logical
    # response, so the streamed terminal payload must field-equal the buffered
    # extract result asserted in tests/test_backend_contract.py.
    events = _drain(_streaming_factory())
    assert events[-1].result is not None
    assert events[-1].result.data == EXPECTED_TERMINAL_DATA


@pytest.mark.unit
def test_gemini_stream_extract_surfaces_http_error_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream stream boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url=GEMINI_BASE_URL)
    adapter = GeminiAdapter(client=client)

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
    assert "Gemini" in message
    assert "500" in message
    assert "upstream stream boom" in message
