"""OllamaAdapter — BackendAdapter against a local Ollama server.

Uses httpx directly (no ollama SDK) so the dependency surface stays small and
tests can inject ``httpx.MockTransport`` for cassette replay.

Two construction modes:
- ``base_url=...`` (defaults to ``http://localhost:11434``) builds an owned
  ``httpx.AsyncClient``. Local Ollama has no auth by default, so no headers
  are injected.
- ``client=...`` accepts a caller-built client (typically with MockTransport).

Structured output is forced via Ollama's native ``format`` field on
``POST /api/chat``, which accepts a JSON Schema (Ollama v0.5+). The model is
constrained to emit a JSON string conforming to the schema; ``message.content``
in the response is that JSON string, which we ``json.loads`` into a dict.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx

from prompiler.backends._pipeline import post_with_retry, truncate_for_error
from prompiler.backends.observability import (
    DEFAULT_PRICING_TABLE,
    ObservabilityHook,
    PricingTable,
    emit_call_metrics,
)
from prompiler.backends.retry import RetryPolicy

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"


class OllamaAdapter:
    def __init__(
        self,
        *,
        base_url: str = OLLAMA_BASE_URL,
        client: httpx.AsyncClient | None = None,
        model: str = DEFAULT_MODEL,
        retry_policy: RetryPolicy | None = None,
        observability: ObservabilityHook | None = None,
        pricing: PricingTable | None = None,
    ) -> None:
        self._model = model
        self._retry_policy = retry_policy or RetryPolicy()
        self._observability = observability
        self._pricing = pricing or DEFAULT_PRICING_TABLE
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={"content-type": "application/json"},
            )
            self._owns_client = True

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "format": json_schema,
            "stream": False,
        }

        body, latency = await post_with_retry(
            client=self._client,
            path="/api/chat",
            payload=payload,
            vendor_label="Ollama",
            retry_policy=self._retry_policy,
            timeout=timeout,
        )
        message = body.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError(
                f"Ollama response missing message.content string: {truncate_for_error(body)}"
            )
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RuntimeError(
                f"Ollama message.content did not decode to dict: {truncate_for_error(content)}"
            )
        await emit_call_metrics(
            hook=self._observability,
            backend="ollama",
            model=self._model,
            latency_seconds=latency,
            prompt_tokens=int(body.get("prompt_eval_count", 0)),
            completion_tokens=int(body.get("eval_count", 0)),
            pricing=self._pricing,
        )
        return parsed

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
