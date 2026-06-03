"""GeminiAdapter — BackendAdapter against Google Generative Language API.

Uses httpx directly (no google SDK) so the dependency surface stays small and
tests can inject ``httpx.MockTransport`` for cassette replay.

Two construction modes:
- ``api_key=...`` builds an owned ``httpx.AsyncClient`` with auth headers
- ``client=...`` accepts a caller-built client (typically with MockTransport)

Structured output is forced via ``toolConfig.functionCallingConfig`` with
``mode="ANY"`` and ``allowedFunctionNames=["extract"]``. The response is
guaranteed to contain a part with a ``functionCall`` whose ``args`` is a dict
conforming to the supplied JSON Schema.
"""

from __future__ import annotations

import copy
import time
from typing import Any, cast

import httpx

from prompiler.backends.credentials import CredentialError, CredentialProvider
from prompiler.backends.observability import (
    DEFAULT_PRICING_TABLE,
    ObservabilityHook,
    PricingTable,
    emit_call_metrics,
)
from prompiler.backends.retry import RetryPolicy, with_retry

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-2.5-flash"
EXTRACT_TOOL_NAME = "extract"
EXTRACT_TOOL_DESCRIPTION = "Return structured data matching the provided JSON Schema."

GEMINI_MAX_DEPTH = 5
GEMINI_DROP_KEYS = frozenset(
    {
        "pattern",
        "format",
        "additionalProperties",
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "definitions",
        "multipleOf",
    }
)
_RECURSE_KEYS = frozenset({"properties", "items", "anyOf", "oneOf", "allOf"})


def _project_gemini(node: Any, *, depth: int) -> Any:
    """Strip Gemini-hostile keys and cap nesting at ``GEMINI_MAX_DEPTH``.

    ``depth`` is the 1-indexed level of ``node``. When ``depth`` reaches the
    cap, structural keys (``properties``/``items``/combinator lists) are
    dropped rather than recursed into — the resulting tree height stays
    within Gemini's OpenAPI-3-derived limit.
    """
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    out: dict[str, Any] = {}
    at_max = depth >= GEMINI_MAX_DEPTH
    for key, value in node.items():
        if key in GEMINI_DROP_KEYS:
            continue
        if key in _RECURSE_KEYS and at_max:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _project_gemini(v, depth=depth + 1) for k, v in value.items()}
        elif key == "items":
            out[key] = _project_gemini(value, depth=depth + 1)
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            out[key] = [_project_gemini(v, depth=depth + 1) for v in value]
        else:
            out[key] = copy.deepcopy(value)
    return out


class GeminiAdapter:
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
            auth_headers = {"x-goog-api-key": api_key}
        elif credentials is not None:
            auth_headers = dict(credentials.resolve("gemini").headers)
        else:
            raise CredentialError(
                "GeminiAdapter requires api_key, client, or credentials; "
                "see docs/MANUAL_TESTING.md §3 (credentials)"
            )
        self._client = httpx.AsyncClient(
            base_url=GEMINI_BASE_URL,
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
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": EXTRACT_TOOL_NAME,
                            "description": EXTRACT_TOOL_DESCRIPTION,
                            "parameters": json_schema,
                        }
                    ]
                }
            ],
            "toolConfig": {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [EXTRACT_TOOL_NAME],
                }
            },
        }
        path = f"/v1beta/models/{self._model}:generateContent"

        async def _do_post() -> httpx.Response:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
            return response

        started = time.perf_counter()
        response = await with_retry(_do_post, policy=self._retry_policy)
        latency = time.perf_counter() - started
        body: dict[str, Any] = response.json()
        for candidate in body.get("candidates", []):
            content = candidate.get("content") or {}
            for part in content.get("parts", []):
                function_call = part.get("functionCall")
                if not isinstance(function_call, dict):
                    continue
                if function_call.get("name") != EXTRACT_TOOL_NAME:
                    continue
                args = function_call.get("args")
                if isinstance(args, dict):
                    usage = body.get("usageMetadata") or {}
                    await emit_call_metrics(
                        hook=self._observability,
                        backend="gemini",
                        model=self._model,
                        latency_seconds=latency,
                        prompt_tokens=int(usage.get("promptTokenCount", 0)),
                        completion_tokens=int(usage.get("candidatesTokenCount", 0)),
                        pricing=self._pricing,
                    )
                    return args
        raise RuntimeError(
            f"Gemini response missing functionCall for {EXTRACT_TOOL_NAME!r}: {body!r}"
        )

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return cast("dict[str, Any]", _project_gemini(json_schema, depth=1))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
