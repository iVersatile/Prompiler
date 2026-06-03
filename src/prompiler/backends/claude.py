"""ClaudeAdapter — BackendAdapter against Anthropic Messages API.

Uses httpx directly (no anthropic SDK) to keep the dependency surface small
and to let tests inject `httpx.MockTransport` for cassette replay.

Two construction modes:
- `api_key=...` builds an owned `httpx.AsyncClient` with auth headers
- `client=...` accepts a caller-built client (typically with MockTransport)

Structured output is forced via `tool_choice={"type": "tool", "name": "extract"}`,
so the response is guaranteed to contain a `tool_use` block whose `input`
conforms to the supplied JSON Schema.
"""

from __future__ import annotations

import copy
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

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 4096
EXTRACT_TOOL_NAME = "extract"
EXTRACT_TOOL_DESCRIPTION = "Return structured data matching the provided JSON Schema."


class ClaudeAdapter:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        credentials: CredentialProvider | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        retry_policy: RetryPolicy | None = None,
        observability: ObservabilityHook | None = None,
        pricing: PricingTable | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._retry_policy = retry_policy or RetryPolicy()
        self._observability = observability
        self._pricing = pricing or DEFAULT_PRICING_TABLE
        if client is not None:
            self._client = client
            self._owns_client = False
            return
        auth_headers: dict[str, str]
        if api_key is not None:
            auth_headers = {"x-api-key": api_key}
        elif credentials is not None:
            auth_headers = dict(credentials.resolve("claude").headers)
        else:
            raise CredentialError(
                "ClaudeAdapter requires api_key, client, or credentials; "
                "see docs/MANUAL_TESTING.md §3 (credentials)"
            )
        self._client = httpx.AsyncClient(
            base_url=ANTHROPIC_BASE_URL,
            headers={
                **auth_headers,
                "anthropic-version": ANTHROPIC_VERSION,
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
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": EXTRACT_TOOL_NAME,
                    "description": EXTRACT_TOOL_DESCRIPTION,
                    "input_schema": json_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": EXTRACT_TOOL_NAME},
        }

        async def _do_post() -> httpx.Response:
            response = await self._client.post("/v1/messages", json=payload)
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"Claude {response.status_code}: {response.text}",
                    request=response.request,
                    response=response,
                )
            return response

        started = time.perf_counter()
        response = await with_retry(_do_post, policy=self._retry_policy)
        latency = time.perf_counter() - started
        body: dict[str, Any] = response.json()
        for block in body.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == EXTRACT_TOOL_NAME:
                tool_input = block.get("input")
                if isinstance(tool_input, dict):
                    usage = body.get("usage") or {}
                    await emit_call_metrics(
                        hook=self._observability,
                        backend="claude",
                        model=self._model,
                        latency_seconds=latency,
                        prompt_tokens=int(usage.get("input_tokens", 0)),
                        completion_tokens=int(usage.get("output_tokens", 0)),
                        pricing=self._pricing,
                    )
                    return tool_input
        raise RuntimeError(
            f"Claude response missing tool_use block for {EXTRACT_TOOL_NAME!r}: {body!r}"
        )

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
