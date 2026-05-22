"""OpenAIAdapter — BackendAdapter against OpenAI Chat Completions API.

Uses httpx directly (no openai SDK) so the dependency surface stays small and
tests can inject ``httpx.MockTransport`` for cassette replay.

Two construction modes:
- ``api_key=...`` builds an owned ``httpx.AsyncClient`` with auth headers
- ``client=...`` accepts a caller-built client (typically with MockTransport)

Structured output is forced via ``tool_choice={"type": "function", "function":
{"name": "extract"}}``. The response is guaranteed to contain a tool call
whose ``function.arguments`` is a JSON string conforming to the supplied JSON
Schema; we parse it into a ``dict[str, Any]``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx

from prompiler.backends.credentials import CredentialError, CredentialProvider

OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_MODEL = "gpt-4o-mini"
EXTRACT_TOOL_NAME = "extract"
EXTRACT_TOOL_DESCRIPTION = "Return structured data matching the provided JSON Schema."


class OpenAIAdapter:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        credentials: CredentialProvider | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._model = model
        if client is not None:
            self._client = client
            self._owns_client = False
            return
        auth_headers: dict[str, str]
        if api_key is not None:
            auth_headers = {"authorization": f"Bearer {api_key}"}
        elif credentials is not None:
            auth_headers = dict(credentials.resolve("openai").headers)
        else:
            raise CredentialError(
                "OpenAIAdapter requires api_key, client, or credentials; "
                "see docs/MANUAL_TESTING.md §3 (credentials)"
            )
        self._client = httpx.AsyncClient(
            base_url=OPENAI_BASE_URL,
            headers={
                **auth_headers,
                "content-type": "application/json",
            },
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
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": EXTRACT_TOOL_NAME,
                        "description": EXTRACT_TOOL_DESCRIPTION,
                        "parameters": json_schema,
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": EXTRACT_TOOL_NAME},
            },
        }
        response = await self._client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        for choice in body.get("choices", []):
            message = choice.get("message") or {}
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                if function.get("name") != EXTRACT_TOOL_NAME:
                    continue
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    continue
                parsed = json.loads(arguments)
                if isinstance(parsed, dict):
                    return parsed
        raise RuntimeError(
            f"OpenAI response missing tool_calls entry for {EXTRACT_TOOL_NAME!r}: {body!r}"
        )

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
