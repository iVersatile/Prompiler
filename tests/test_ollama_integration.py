"""Live Ollama sidecar integration test.

Hits a real Ollama server over HTTP and verifies that ``OllamaAdapter`` can
drive a structured-output ``extract`` call end-to-end against the wire. CI
runs this against an ``ollama/ollama`` service container with a small model
pre-pulled; locally the test self-skips when the readiness probe fails so
``pytest -m integration`` stays green on dev boxes without Ollama running.

Configuration via env vars:
- ``OLLAMA_INTEGRATION_BASE_URL`` (default ``http://localhost:11434``)
- ``OLLAMA_INTEGRATION_MODEL`` (default ``llama3.2:1b`` — small + structured-
  output capable; CI pulls this model in a pre-test step)
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from prompiler.backends.observability import BackendCallMetrics
from prompiler.backends.ollama import OllamaAdapter

pytestmark = pytest.mark.integration

OLLAMA_BASE_URL = os.environ.get("OLLAMA_INTEGRATION_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_INTEGRATION_MODEL", "llama3.2:1b")
READINESS_TIMEOUT_SECONDS = 2.0


def _ollama_ready() -> bool:
    try:
        response = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=READINESS_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


if not _ollama_ready():
    pytest.skip(
        f"Ollama unreachable at {OLLAMA_BASE_URL}; set OLLAMA_INTEGRATION_BASE_URL "
        "or run an Ollama server to enable this test.",
        allow_module_level=True,
    )


class _RecordingHook:
    def __init__(self) -> None:
        self.calls: list[BackendCallMetrics] = []

    async def on_call(self, metrics: BackendCallMetrics) -> None:
        self.calls.append(metrics)


def test_ollama_extract_against_live_server() -> None:
    """End-to-end: real HTTP, real model, structured output dict comes back."""
    hook = _RecordingHook()

    async def _scenario() -> dict[str, object]:
        adapter = OllamaAdapter(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            observability=hook,
        )
        try:
            return await adapter.extract(
                prompt='Classify the sentiment of "I love this product" as one of '
                '"positive", "negative", or "neutral". Respond with JSON.',
                json_schema={
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        }
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
            )
        finally:
            await adapter.aclose()

    result = asyncio.run(_scenario())

    assert isinstance(result, dict)
    assert "label" in result
    assert result["label"] in {"positive", "negative", "neutral"}

    assert len(hook.calls) == 1
    metrics = hook.calls[0]
    assert metrics.backend == "ollama"
    assert metrics.model == OLLAMA_MODEL
    assert metrics.latency_seconds > 0
    assert metrics.prompt_tokens > 0
    assert metrics.completion_tokens > 0
    assert metrics.cost_usd == 0.0
