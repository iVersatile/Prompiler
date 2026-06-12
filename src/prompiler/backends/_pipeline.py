"""Shared adapter pipeline — POST → retry → JSON → latency.

The four real adapters (claude, openai, gemini, ollama) all share the same
plumbing around a single POST: build kwargs (with optional ``timeout``),
raise vendor-prefixed ``HTTPStatusError`` on 4xx/5xx (LL-003), wrap in
``with_retry``, measure latency, and decode JSON. ``post_with_retry`` is
that plumbing; vendor adapters keep the parts that actually differ —
payload shape, path, and response parsing.

``truncate_for_error`` clamps response bodies before they land in
``RuntimeError`` messages so a 100 KB cassette body doesn't flood the
traceback.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from prompiler.backends.retry import RetryPolicy, with_retry


async def post_with_retry(
    *,
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    vendor_label: str,
    retry_policy: RetryPolicy,
    timeout: float | None = None,
) -> tuple[dict[str, Any], float]:
    """POST ``payload`` to ``path``, retry transient errors, return ``(body, latency)``.

    ``vendor_label`` prefixes the ``HTTPStatusError`` message on 4xx/5xx so
    error visibility (LL-003) survives the retry wrapper. ``timeout`` is
    threaded conditionally — ``None`` means "use the client's default"
    rather than "no timeout" (httpx treats explicit ``timeout=None`` as
    unbounded).
    """

    async def _do_post() -> httpx.Response:
        post_kwargs: dict[str, Any] = {"json": payload}
        if timeout is not None:
            post_kwargs["timeout"] = timeout
        response = await client.post(path, **post_kwargs)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{vendor_label} {response.status_code}: {truncate_for_error(response.text)}",
                request=response.request,
                response=response,
            )
        return response

    started = time.perf_counter()
    response = await with_retry(_do_post, policy=retry_policy)
    latency = time.perf_counter() - started
    body: dict[str, Any] = response.json()
    return body, latency


async def iter_sse_data(
    *,
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    vendor_label: str,
    timeout: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a POST as Server-Sent Events, yielding each ``data:`` frame's JSON.

    The streaming sibling of ``post_with_retry``: open ``client.stream``, raise
    the same vendor-prefixed ``HTTPStatusError`` on 4xx/5xx (LL-003, reading the
    body first so the message carries it), then yield one decoded dict per
    non-empty ``data:`` line. ``event:`` and blank framing lines fall through the
    ``data:`` guard. No retry: a partially-consumed stream cannot be safely
    replayed — retry parity is deferred to G4.
    """
    stream_kwargs: dict[str, Any] = {"json": payload}
    if timeout is not None:
        stream_kwargs["timeout"] = timeout
    async with client.stream("POST", path, **stream_kwargs) as response:
        if response.status_code >= 400:
            await response.aread()
            raise httpx.HTTPStatusError(
                f"{vendor_label} {response.status_code}: {truncate_for_error(response.text)}",
                request=response.request,
                response=response,
            )
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data:
                continue
            yield json.loads(data)


def truncate_for_error(value: Any, *, limit: int = 200) -> str:
    """Return ``repr(value)`` clamped to ``limit`` chars for error messages."""
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"
