"""Unit tests for the shared adapter pipeline (``post_with_retry``).

Covers behaviour that lives in ``_pipeline.py`` directly — the parts every
real adapter inherits without exercising vendor-specific shapes:

- **timeout threading (MEDIUM-2)**: ``timeout`` is forwarded to
  ``client.post(...)`` so callers can override the client default per call.
  Stripping the conditional branch in ``_do_post`` leaves the client default
  in place, which this test catches.

- **HTTPStatusError body cap (HIGH-1, LL-003)**: vendor 4xx/5xx bodies can
  carry footers / tokens / multi-KB payloads. ``HTTPStatusError.args[0]``
  must stay bounded so that future P3 observability that logs
  ``exc.args[0]`` does not leak the raw body verbatim.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from prompiler.backends._pipeline import post_with_retry
from prompiler.backends.retry import RetryPolicy


@pytest.mark.unit
def test_post_with_retry_threads_timeout_into_client_post() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="https://example.test",
        timeout=99.0,
    )

    try:
        asyncio.run(
            post_with_retry(
                client=client,
                path="/v1/x",
                payload={"k": "v"},
                vendor_label="Test",
                retry_policy=RetryPolicy(max_attempts=1),
                timeout=7.5,
            )
        )
    finally:
        asyncio.run(client.aclose())

    timeout_ext = captured["timeout"]
    assert isinstance(timeout_ext, dict)
    assert timeout_ext.get("read") == pytest.approx(7.5)
    assert timeout_ext.get("connect") == pytest.approx(7.5)


@pytest.mark.unit
def test_post_with_retry_omits_timeout_when_none() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="https://example.test",
        timeout=99.0,
    )

    try:
        asyncio.run(
            post_with_retry(
                client=client,
                path="/v1/x",
                payload={"k": "v"},
                vendor_label="Test",
                retry_policy=RetryPolicy(max_attempts=1),
                timeout=None,
            )
        )
    finally:
        asyncio.run(client.aclose())

    timeout_ext = captured["timeout"]
    assert isinstance(timeout_ext, dict)
    assert timeout_ext.get("read") == pytest.approx(99.0)


@pytest.mark.unit
def test_post_with_retry_caps_response_body_in_http_status_error() -> None:
    huge_body = "X" * 100_000

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=huge_body.encode())

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://example.test")

    try:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            asyncio.run(
                post_with_retry(
                    client=client,
                    path="/v1/x",
                    payload={"k": "v"},
                    vendor_label="Test",
                    retry_policy=RetryPolicy(max_attempts=1),
                )
            )
    finally:
        asyncio.run(client.aclose())

    message = excinfo.value.args[0]
    assert isinstance(message, str)
    assert "Test 403" in message
    assert "...(truncated)" in message
    assert len(message) < 1024
    assert huge_body not in message
