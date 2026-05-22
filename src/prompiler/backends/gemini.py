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

from typing import Any

import httpx

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-1.5-flash"
EXTRACT_TOOL_NAME = "extract"
EXTRACT_TOOL_DESCRIPTION = "Return structured data matching the provided JSON Schema."


class GeminiAdapter:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        if api_key is None and client is None:
            raise ValueError("GeminiAdapter requires api_key or client")
        self._model = model
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            assert api_key is not None
            self._client = httpx.AsyncClient(
                base_url=GEMINI_BASE_URL,
                headers={
                    "x-goog-api-key": api_key,
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
        response = await self._client.post(path, json=payload)
        response.raise_for_status()
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
                    return args
        raise RuntimeError(
            f"Gemini response missing functionCall for {EXTRACT_TOOL_NAME!r}: {body!r}"
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
