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

import json
from typing import Any

import httpx

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"


class OllamaAdapter:
    def __init__(
        self,
        *,
        base_url: str = OLLAMA_BASE_URL,
        client: httpx.AsyncClient | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._model = model
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
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "format": json_schema,
            "stream": False,
        }
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        message = body.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError(f"Ollama response missing message.content string: {body!r}")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Ollama message.content did not decode to dict: {content!r}")
        return parsed

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
