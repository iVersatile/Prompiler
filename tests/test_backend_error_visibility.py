"""Cross-adapter contract: 4xx error must expose response body to the caller.

Applies LL-003. ``httpx.Response.raise_for_status()`` raises an
``HTTPStatusError`` whose default message is the generic
``"Client error '<status> <reason>' for url '<url>'"`` — the response *body*
is dropped on the floor. When the body carries the real diagnostic (e.g.
Gemini's ``INVALID_ARGUMENT`` text naming the offending JSON-Schema key) the
caller sees nothing useful unless we embed it explicitly.

This test pins the contract for every adapter that speaks HTTP: the
``HTTPStatusError`` message produced on a 4xx must contain a marker drawn
from the response body. Parametrised across Claude / OpenAI / Gemini /
Ollama so a regression in any single adapter is caught uniformly. MockAdapter
is excluded — it has no HTTP layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from prompiler.backends.claude import ANTHROPIC_BASE_URL, ClaudeAdapter
from prompiler.backends.gemini import GEMINI_BASE_URL, GeminiAdapter
from prompiler.backends.ollama import OLLAMA_BASE_URL, OllamaAdapter
from prompiler.backends.openai import OPENAI_BASE_URL, OpenAIAdapter
from prompiler.backends.retry import RetryPolicy

BODY_MARKER = "probe-body-marker-1d2e3f"
ERROR_BODY = f'{{"error":{{"message":"{BODY_MARKER}"}}}}'

PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
}

NO_RETRY = RetryPolicy(max_attempts=1)


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, text=ERROR_BODY)


def _claude_factory() -> tuple[Any, httpx.AsyncClient]:
    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, base_url=ANTHROPIC_BASE_URL)
    return ClaudeAdapter(client=client, retry_policy=NO_RETRY), client


def _openai_factory() -> tuple[Any, httpx.AsyncClient]:
    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, base_url=OPENAI_BASE_URL)
    return OpenAIAdapter(client=client, retry_policy=NO_RETRY), client


def _gemini_factory() -> tuple[Any, httpx.AsyncClient]:
    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, base_url=GEMINI_BASE_URL)
    return GeminiAdapter(client=client, retry_policy=NO_RETRY), client


def _ollama_factory() -> tuple[Any, httpx.AsyncClient]:
    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, base_url=OLLAMA_BASE_URL)
    return OllamaAdapter(client=client, retry_policy=NO_RETRY), client


FACTORIES: list[Any] = [
    pytest.param(_claude_factory, id="claude"),
    pytest.param(_openai_factory, id="openai"),
    pytest.param(_gemini_factory, id="gemini"),
    pytest.param(_ollama_factory, id="ollama"),
]


@pytest.mark.unit
@pytest.mark.parametrize("factory", FACTORIES)
def test_4xx_error_message_embeds_response_body(
    factory: Callable[[], tuple[Any, httpx.AsyncClient]],
) -> None:
    adapter, client = factory()

    async def run() -> None:
        try:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await adapter.extract(prompt="probe", json_schema=PROBE_SCHEMA)
            assert BODY_MARKER in str(exc_info.value), (
                "LL-003 regression: 4xx HTTPStatusError dropped response body. "
                f"Got: {exc_info.value!s}"
            )
        finally:
            await client.aclose()

    asyncio.run(run())
