"""Unit tests for BackendAdapter.to_tool_schema projections.

Source of truth: docs/PLAN.md §P2 line 130 — "Per-adapter
``to_tool_schema(json_schema)`` projection (handle backend-specific
degradation: no ``pattern``, no decimals, depth limits)."

Two contracts are exercised here:

  1. Passthrough adapters (Mock / Claude / OpenAI / Ollama) return a *deep
     copy* of the input schema — value-equal but referentially independent
     so caller mutations cannot reach back into the spec author's input.
  2. ``GeminiAdapter`` projects the schema into Gemini's OpenAPI-3-derived
     dialect: drops ``pattern`` / ``format`` / ``additionalProperties`` /
     ``$schema`` / ``$id`` / ``$ref`` / ``$defs`` / ``definitions`` /
     ``multipleOf`` at every level, and caps nesting at five levels.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.gemini import (
    GEMINI_BASE_URL,
    GEMINI_DROP_KEYS,
    GEMINI_MAX_DEPTH,
    GeminiAdapter,
)
from prompiler.backends.mock import MockAdapter
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter

PROBE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "email": {"type": "string", "format": "email", "pattern": r"^.+@.+$"},
        "price": {"type": "number", "multipleOf": 0.01},
        "tags": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[a-z]+$"},
        },
    },
    "required": ["email", "price"],
}


def _deep_nested(depth: int) -> dict[str, Any]:
    """Build an object schema nested ``depth`` levels deep via ``properties``."""
    node: dict[str, Any] = {"type": "string"}
    for _ in range(depth - 1):
        node = {"type": "object", "properties": {"child": node}}
    return node


def _max_depth(node: Any) -> int:
    """Tree height (1-indexed) walking ``properties`` / ``items`` / combinators."""
    if not isinstance(node, dict):
        return 0
    best = 1
    if isinstance(node.get("properties"), dict):
        for child in node["properties"].values():
            best = max(best, 1 + _max_depth(child))
    if "items" in node:
        best = max(best, 1 + _max_depth(node["items"]))
    for key in ("anyOf", "oneOf", "allOf"):
        value = node.get(key)
        if isinstance(value, list):
            for child in value:
                best = max(best, 1 + _max_depth(child))
    return best


def _find_keys(node: Any, hostile: frozenset[str]) -> list[str]:
    """Return every hostile key found anywhere in ``node``."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in hostile:
                found.append(key)
            found.extend(_find_keys(value, hostile))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_keys(item, hostile))
    return found


PASSTHROUGH_FACTORIES = [
    pytest.param(MockAdapter, id="mock"),
    pytest.param(lambda: ClaudeAdapter(api_key="test"), id="claude"),
    pytest.param(lambda: OpenAIAdapter(api_key="test"), id="openai"),
    pytest.param(OllamaAdapter, id="ollama"),
]


@pytest.mark.unit
@pytest.mark.parametrize("factory", PASSTHROUGH_FACTORIES)
def test_passthrough_preserves_schema(factory: Any) -> None:
    adapter = factory()
    out = adapter.to_tool_schema(PROBE_SCHEMA)
    assert out == PROBE_SCHEMA


@pytest.mark.unit
@pytest.mark.parametrize("factory", PASSTHROUGH_FACTORIES)
def test_passthrough_returns_deep_copy(factory: Any) -> None:
    adapter = factory()
    src: dict[str, Any] = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    out = adapter.to_tool_schema(src)
    out["properties"]["x"]["type"] = "integer"
    out["required"].append("y")
    assert src["properties"]["x"]["type"] == "string"
    assert src["required"] == ["x"]


@pytest.mark.unit
def test_gemini_strips_hostile_keys() -> None:
    adapter = GeminiAdapter(api_key="test")
    out = adapter.to_tool_schema(PROBE_SCHEMA)
    leaked = _find_keys(out, GEMINI_DROP_KEYS)
    assert leaked == [], f"hostile keys leaked through Gemini projection: {leaked}"
    assert out["type"] == "object"
    assert set(out["properties"].keys()) == {"email", "price", "tags"}
    assert out["required"] == ["email", "price"]


@pytest.mark.unit
def test_gemini_caps_nesting_depth() -> None:
    adapter = GeminiAdapter(api_key="test")
    deep_input = _deep_nested(depth=9)
    assert _max_depth(deep_input) == 9
    out = adapter.to_tool_schema(deep_input)
    assert _max_depth(out) <= GEMINI_MAX_DEPTH


@pytest.mark.unit
def test_gemini_extract_strips_hostile_keys_on_wire() -> None:
    """Regression: ``extract()`` must route schema through ``to_tool_schema``.

    Prior bug: the live path passed the raw ``json_schema`` into the
    ``functionDeclarations[].parameters`` field, so Gemini-hostile keys
    (``additionalProperties`` et al.) reached the wire and the API rejected
    with HTTP 400 ``INVALID_ARGUMENT``. The projection helper existed but
    the live call site bypassed it.
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        function_call = {
            "name": "extract",
            "args": {"email": "a@b.c", "price": 1.0},
        }
        payload = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"functionCall": function_call}],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url=GEMINI_BASE_URL,
        transport=transport,
        headers={"content-type": "application/json"},
    )
    adapter = GeminiAdapter(client=client)

    async def run() -> dict[str, Any]:
        try:
            return await adapter.extract(prompt="probe", json_schema=PROBE_SCHEMA)
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result.data == {"email": "a@b.c", "price": 1.0}

    body = captured["body"]
    parameters = body["tools"][0]["functionDeclarations"][0]["parameters"]
    leaked = _find_keys(parameters, GEMINI_DROP_KEYS)
    assert leaked == [], f"extract() bypassed to_tool_schema — hostile keys reached wire: {leaked}"
    # Sanity: meaningful structure still present.
    assert parameters["type"] == "object"
    assert set(parameters["properties"].keys()) == {"email", "price", "tags"}
