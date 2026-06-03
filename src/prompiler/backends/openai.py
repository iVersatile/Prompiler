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
import time
from typing import Any

import httpx

from prompiler.backends.credentials import CredentialError, CredentialProvider
from prompiler.backends.observability import (
    DEFAULT_PRICING_TABLE,
    ObservabilityHook,
    PricingTable,
    emit_call_metrics,
)
from prompiler.backends.retry import RetryPolicy, with_retry

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

        async def _do_post() -> httpx.Response:
            response = await self._client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            return response

        started = time.perf_counter()
        response = await with_retry(_do_post, policy=self._retry_policy)
        latency = time.perf_counter() - started
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
                    usage = body.get("usage") or {}
                    await emit_call_metrics(
                        hook=self._observability,
                        backend="openai",
                        model=self._model,
                        latency_seconds=latency,
                        prompt_tokens=int(usage.get("prompt_tokens", 0)),
                        completion_tokens=int(usage.get("completion_tokens", 0)),
                        pricing=self._pricing,
                    )
                    return parsed
        raise RuntimeError(
            f"OpenAI response missing tool_calls entry for {EXTRACT_TOOL_NAME!r}: {body!r}"
        )

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
