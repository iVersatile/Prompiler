"""Shared contract test for BackendAdapter implementations.

Source of truth: docs/PLAN.md §P2 acceptance — "All 4 adapters pass a shared
'happy-path extract' contract test." This file defines that shared test.

Parameterized over adapter factories so every adapter (mock today; claude /
openai / gemini / ollama in later P2.x tasks) runs the same assertions:

  1. Instance satisfies the ``BackendAdapter`` runtime-checkable Protocol.
  2. ``await adapter.extract(...)`` returns a ``dict``.
  3. Every key listed in ``json_schema['required']`` appears in the result.
  4. Two successive calls with identical inputs return identical dicts
     (determinism — needed so MockAdapter can stand in for golden tests and
     so cassette-replayed real adapters stay byte-stable).

Adapter factories are added to ``ADAPTER_FACTORIES`` as each real adapter
lands; the contract assertions never change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from _cassette import CassettePlayer
from _cassette_transport import make_cassette_transport
from prompiler.backends import (
    BackendAdapter,
    ClaudeAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAIAdapter,
)
from prompiler.backends.claude import ANTHROPIC_BASE_URL
from prompiler.backends.gemini import GEMINI_BASE_URL
from prompiler.backends.mock import MockAdapter
from prompiler.backends.ollama import OLLAMA_BASE_URL
from prompiler.backends.openai import OPENAI_BASE_URL

AdapterFactory = Callable[[], BackendAdapter]

CLAUDE_CASSETTE = Path(__file__).parent / "cassettes" / "claude_happy_path.json"
GEMINI_CASSETTE = Path(__file__).parent / "cassettes" / "gemini_happy_path.json"
OLLAMA_CASSETTE = Path(__file__).parent / "cassettes" / "ollama_happy_path.json"
OPENAI_CASSETTE = Path(__file__).parent / "cassettes" / "openai_happy_path.json"


def _claude_factory() -> BackendAdapter:
    player = CassettePlayer.from_json(CLAUDE_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=ANTHROPIC_BASE_URL)
    return ClaudeAdapter(client=client)


def _gemini_factory() -> BackendAdapter:
    player = CassettePlayer.from_json(GEMINI_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=GEMINI_BASE_URL)
    return GeminiAdapter(client=client)


def _openai_factory() -> BackendAdapter:
    player = CassettePlayer.from_json(OPENAI_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=OPENAI_BASE_URL)
    return OpenAIAdapter(client=client)


def _ollama_factory() -> BackendAdapter:
    player = CassettePlayer.from_json(OLLAMA_CASSETTE.read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=OLLAMA_BASE_URL)
    return OllamaAdapter(client=client)


ADAPTER_FACTORIES: list[AdapterFactory] = [
    MockAdapter,
    _claude_factory,
    _gemini_factory,
    _openai_factory,
    _ollama_factory,
]


# A minimal but non-trivial schema: two required string fields plus one
# optional string. Real specs project to shapes like this after the P2
# degradation pass strips unsupported keywords.
HAPPY_PATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "reason": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}

HAPPY_PATH_PROMPT = "Classify the following text into one routing bucket."


@pytest.fixture(params=ADAPTER_FACTORIES, ids=lambda f: f.__name__)
def adapter(request: pytest.FixtureRequest) -> BackendAdapter:
    factory: AdapterFactory = request.param
    return factory()


@pytest.mark.unit
def test_adapter_satisfies_protocol(adapter: BackendAdapter) -> None:
    assert isinstance(adapter, BackendAdapter)


@pytest.mark.unit
def test_extract_returns_dict(adapter: BackendAdapter) -> None:
    result = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    assert isinstance(result, dict)


@pytest.mark.unit
def test_extract_contains_required_keys(adapter: BackendAdapter) -> None:
    result = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    for key in HAPPY_PATH_SCHEMA["required"]:
        assert key in result, f"required key {key!r} missing from {result!r}"


@pytest.mark.unit
def test_extract_is_deterministic(adapter: BackendAdapter) -> None:
    first = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    second = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    assert first == second


@pytest.mark.unit
def test_extract_concurrent_invocations() -> None:
    # Cassette players are ordered+exhaustive (single-shot), so concurrency
    # is exercised against MockAdapter only — proves the adapter contract is
    # safe under asyncio.gather without re-entrancy bugs.
    adapter = MockAdapter()

    async def _run() -> list[dict[str, Any]]:
        return await asyncio.gather(
            *[
                adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA)
                for _ in range(5)
            ]
        )

    results = asyncio.run(_run())
    assert len(results) == 5
    for result in results:
        assert isinstance(result, dict)
        for key in HAPPY_PATH_SCHEMA["required"]:
            assert key in result
    assert all(r == results[0] for r in results)
