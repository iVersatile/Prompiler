"""Nightly live-smoke checks against real vendor APIs.

Each test self-skips when its required credential or readiness probe is
absent so that ``pytest -m live_smoke`` is a no-op on dev boxes and PR CI
runs. The nightly workflow (``.github/workflows/nightly-live-smoke.yml``)
provides the secrets and the Ollama sidecar; everything else stays green
without them.

These tests exist to catch silent vendor breakage (model deprecation, schema
projection drift, auth-header changes) — they are intentionally minimal:
one happy-path ``extract`` per backend against a small, cheap model.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest

from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.gemini import GeminiAdapter
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter

pytestmark = pytest.mark.live_smoke

SENTIMENT_PROMPT = (
    'Classify the sentiment of "I love this product" as one of '
    '"positive", "negative", or "neutral". Respond with JSON.'
)
SENTIMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "enum": ["positive", "negative", "neutral"],
        }
    },
    "required": ["label"],
    "additionalProperties": False,
}

OLLAMA_LIVE_SMOKE_BASE_URL = os.environ.get("OLLAMA_LIVE_SMOKE_BASE_URL", "http://localhost:11434")
OLLAMA_LIVE_SMOKE_MODEL = os.environ.get("OLLAMA_LIVE_SMOKE_MODEL", "llama3.2:1b")
OLLAMA_READINESS_TIMEOUT_SECONDS = 2.0


def _assert_sentiment_result(result: Any) -> None:
    assert isinstance(result, dict)
    assert "label" in result
    assert result["label"] in {"positive", "negative", "neutral"}


def _require_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(f"{var} not set; live-smoke for this backend disabled.")
    return value


def test_claude_live_smoke() -> None:
    api_key = _require_env("ANTHROPIC_API_KEY")

    async def _scenario() -> dict[str, Any]:
        adapter = ClaudeAdapter(api_key=api_key)
        try:
            return await adapter.extract(
                prompt=SENTIMENT_PROMPT,
                json_schema=SENTIMENT_SCHEMA,
            )
        finally:
            await adapter.aclose()

    _assert_sentiment_result(asyncio.run(_scenario()))


def test_openai_live_smoke() -> None:
    api_key = _require_env("OPENAI_API_KEY")

    async def _scenario() -> dict[str, Any]:
        adapter = OpenAIAdapter(api_key=api_key)
        try:
            return await adapter.extract(
                prompt=SENTIMENT_PROMPT,
                json_schema=SENTIMENT_SCHEMA,
            )
        finally:
            await adapter.aclose()

    _assert_sentiment_result(asyncio.run(_scenario()))


def test_gemini_live_smoke() -> None:
    api_key = _require_env("GOOGLE_API_KEY")

    async def _scenario() -> dict[str, Any]:
        adapter = GeminiAdapter(api_key=api_key)
        try:
            return await adapter.extract(
                prompt=SENTIMENT_PROMPT,
                json_schema=SENTIMENT_SCHEMA,
            )
        finally:
            await adapter.aclose()

    _assert_sentiment_result(asyncio.run(_scenario()))


def _ollama_ready() -> bool:
    try:
        response = httpx.get(
            f"{OLLAMA_LIVE_SMOKE_BASE_URL}/api/tags",
            timeout=OLLAMA_READINESS_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def test_ollama_live_smoke() -> None:
    if not _ollama_ready():
        pytest.skip(
            f"Ollama unreachable at {OLLAMA_LIVE_SMOKE_BASE_URL}; "
            "set OLLAMA_LIVE_SMOKE_BASE_URL or run an Ollama server "
            "to enable this test."
        )

    async def _scenario() -> dict[str, Any]:
        adapter = OllamaAdapter(
            base_url=OLLAMA_LIVE_SMOKE_BASE_URL,
            model=OLLAMA_LIVE_SMOKE_MODEL,
        )
        try:
            return await adapter.extract(
                prompt=SENTIMENT_PROMPT,
                json_schema=SENTIMENT_SCHEMA,
            )
        finally:
            await adapter.aclose()

    _assert_sentiment_result(asyncio.run(_scenario()))
