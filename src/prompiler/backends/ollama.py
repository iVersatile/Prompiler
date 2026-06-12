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

import base64
import copy
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from prompiler.backends._pipeline import iter_ndjson_data, post_with_retry, truncate_for_error
from prompiler.backends.base import CapabilityError, ExtractResult, ModalContent, StreamEvent
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
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> ExtractResult:
        message: dict[str, Any] = {"role": "user", "content": prompt}
        images: list[str] = []
        for part in modal_parts:
            if part.modality == "audio":
                raise CapabilityError(
                    "OllamaAdapter cannot project audio input; only Gemini supports audio"
                )
            images.append(base64.b64encode(part.data).decode("ascii"))
        if images:
            message["images"] = images
        options: dict[str, Any] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [message],
            "format": json_schema,
            "stream": False,
            "options": options,
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
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama message.content not valid JSON: {truncate_for_error(content)}"
            ) from exc
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
        return ExtractResult(
            data=parsed,
            system_fingerprint=None,
            deterministic=seed is not None,
        )

    async def stream_extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> AsyncIterator[StreamEvent]:
        """Stream the same extract as ``extract``, yielding deltas then a terminal event.

        Drives ``/api/chat`` with ``"stream": True`` and decodes the NDJSON chunk
        sequence via ``iter_ndjson_data``: each chunk's ``message.content`` is a
        string fragment, re-emitted as a non-terminal ``StreamEvent`` and
        accumulated. The fragments concatenate to the same JSON string buffered
        ``extract`` parses, surfaced in one terminal ``StreamEvent``. Usage is
        captured from the terminal ``"done": true`` chunk's ``prompt_eval_count``
        / ``eval_count`` for metric parity. No retry — a partially consumed
        stream cannot be safely replayed (G4).
        """
        message: dict[str, Any] = {"role": "user", "content": prompt}
        images: list[str] = []
        for part in modal_parts:
            if part.modality == "audio":
                raise CapabilityError(
                    "OllamaAdapter cannot project audio input; only Gemini supports audio"
                )
            images.append(base64.b64encode(part.data).decode("ascii"))
        if images:
            message["images"] = images
        options: dict[str, Any] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [message],
            "format": json_schema,
            "stream": True,
            "options": options,
        }

        fragments: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0
        started = time.perf_counter()
        async for frame in iter_ndjson_data(
            client=self._client,
            path="/api/chat",
            payload=payload,
            vendor_label="Ollama",
            timeout=timeout,
        ):
            chunk = frame.get("message") or {}
            content = chunk.get("content")
            if isinstance(content, str) and content != "":
                fragments.append(content)
                yield StreamEvent(delta=content)
            if frame.get("done"):
                prompt_tokens = int(frame.get("prompt_eval_count", 0))
                completion_tokens = int(frame.get("eval_count", 0))
        latency = time.perf_counter() - started

        assembled = "".join(fragments)
        try:
            parsed = json.loads(assembled)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama streamed content not valid JSON: {truncate_for_error(assembled)}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(
                f"Ollama streamed content did not decode to dict: {truncate_for_error(assembled)}"
            )
        await emit_call_metrics(
            hook=self._observability,
            backend="ollama",
            model=self._model,
            latency_seconds=latency,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            pricing=self._pricing,
        )
        yield StreamEvent(
            delta="",
            result=ExtractResult(
                data=parsed,
                system_fingerprint=None,
                deterministic=seed is not None,
            ),
        )

    def supports(self, feature: str) -> bool:
        return feature in {"seed", "multimodal", "streaming"}

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
