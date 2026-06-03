"""Cross-adapter contract: malformed JSON in a 200 response must be classified.

Applies LL-003 (vendor error visibility/classification). When a vendor returns
HTTP 200 but the JSON payload that should carry the structured output is not
valid JSON, the adapter must:

1. Raise ``RuntimeError`` — not a bare ``json.JSONDecodeError``. ``RuntimeError``
   is the classification surface ``ExtractionFailed`` will wrap; a bare decode
   error leaks an implementation detail and bypasses classification.
2. Embed a vendor prefix and the "not valid JSON" marker so the operator sees
   *what* failed without grepping the chained cause.
3. Chain the original ``JSONDecodeError`` via ``__cause__`` so the underlying
   parse position is recoverable.
4. Bound the error message — huge garbage payloads must be truncated, not
   embedded verbatim into the exception message.

Parametrised across OpenAI / Ollama because both adapters parse a JSON string
extracted from the vendor envelope. Claude (tool_use ``input`` is already a
dict) and Gemini (functionCall ``args`` is already a dict) do not run
``json.loads`` on the response payload, so they are excluded.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from prompiler.backends.ollama import OLLAMA_BASE_URL, OllamaAdapter
from prompiler.backends.openai import OPENAI_BASE_URL, OpenAIAdapter
from prompiler.backends.retry import RetryPolicy

MALFORMED_JSON = "{not valid json,,,"

PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
}

NO_RETRY = RetryPolicy(max_attempts=1)


def _openai_body(arguments: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "extract",
                                "arguments": arguments,
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }


def _ollama_body(content: str) -> dict[str, Any]:
    return {
        "message": {"content": content},
        "prompt_eval_count": 0,
        "eval_count": 0,
    }


def _make_openai_factory(
    arguments: str,
) -> Callable[[], tuple[Any, httpx.AsyncClient]]:
    def factory() -> tuple[Any, httpx.AsyncClient]:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_openai_body(arguments))

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, base_url=OPENAI_BASE_URL)
        return OpenAIAdapter(client=client, retry_policy=NO_RETRY), client

    return factory


def _make_ollama_factory(
    content: str,
) -> Callable[[], tuple[Any, httpx.AsyncClient]]:
    def factory() -> tuple[Any, httpx.AsyncClient]:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ollama_body(content))

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, base_url=OLLAMA_BASE_URL)
        return OllamaAdapter(client=client, retry_policy=NO_RETRY), client

    return factory


CLASSIFICATION_FACTORIES: list[Any] = [
    pytest.param(
        _make_openai_factory(MALFORMED_JSON),
        "OpenAI",
        id="openai",
    ),
    pytest.param(
        _make_ollama_factory(MALFORMED_JSON),
        "Ollama",
        id="ollama",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("factory,vendor_prefix", CLASSIFICATION_FACTORIES)
def test_malformed_json_raises_classified_runtime_error(
    factory: Callable[[], tuple[Any, httpx.AsyncClient]],
    vendor_prefix: str,
) -> None:
    adapter, client = factory()

    async def run() -> None:
        try:
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.extract(prompt="probe", json_schema=PROBE_SCHEMA)
            message = str(exc_info.value)
            assert vendor_prefix in message, (
                f"LL-003 regression: malformed-JSON RuntimeError missing vendor "
                f"prefix {vendor_prefix!r}. Got: {message!r}"
            )
            assert "not valid JSON" in message, (
                f"LL-003 regression: malformed-JSON RuntimeError missing "
                f"'not valid JSON' marker. Got: {message!r}"
            )
            assert isinstance(exc_info.value.__cause__, json.JSONDecodeError), (
                f"LL-003 regression: malformed-JSON RuntimeError did not chain "
                f"JSONDecodeError via __cause__. Got: {exc_info.value.__cause__!r}"
            )
        finally:
            await client.aclose()

    asyncio.run(run())


BOUNDED_FACTORIES: list[Any] = [
    pytest.param(_make_openai_factory("X" * 100_000), id="openai"),
    pytest.param(_make_ollama_factory("X" * 100_000), id="ollama"),
]


@pytest.mark.unit
@pytest.mark.parametrize("factory", BOUNDED_FACTORIES)
def test_malformed_json_error_message_is_bounded(
    factory: Callable[[], tuple[Any, httpx.AsyncClient]],
) -> None:
    adapter, client = factory()

    async def run() -> None:
        try:
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.extract(prompt="probe", json_schema=PROBE_SCHEMA)
            message = str(exc_info.value)
            assert "...(truncated)" in message, (
                f"LL-003 regression: malformed-JSON RuntimeError did not "
                f"truncate huge payload. Message length: {len(message)}"
            )
            assert len(message) < 1024, (
                f"LL-003 regression: malformed-JSON RuntimeError message "
                f"unbounded. Length: {len(message)}"
            )
            assert "X" * 1000 not in message, (
                "LL-003 regression: raw garbage payload leaked into RuntimeError message verbatim"
            )
        finally:
            await client.aclose()

    asyncio.run(run())
