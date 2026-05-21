"""Tests for prompiler.mcp.server (P0 L63).

Covers:
- ``GET /healthz`` returns 200 + ``{"status":"ok"}`` + JSON content type.
- Non-``/healthz`` paths return 404.
- Default bind is loopback (``127.0.0.1``).
- Non-loopback host without opt-in raises ``ValueError``.
- Opt-in non-loopback emits exactly one WARN record on
  ``prompiler.mcp.server`` with ``extra["event"] == "mcp.bind.non_loopback"``.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

from prompiler.mcp.server import (
    HEALTHZ_BODY,
    HEALTHZ_PATH,
    LOOPBACK_HOST,
    build_server,
)


@pytest.fixture
def running_server() -> Iterator[ThreadingHTTPServer]:
    server = build_server(host=LOOPBACK_HOST, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.unit
def test_build_server_defaults_to_loopback() -> None:
    server = build_server()
    try:
        host, _port = server.socket.getsockname()[:2]
        assert host == LOOPBACK_HOST
    finally:
        server.server_close()


@pytest.mark.unit
def test_build_server_rejects_non_loopback_without_opt_in() -> None:
    with pytest.raises(ValueError, match="non-loopback"):
        build_server(host="0.0.0.0", port=0)


@pytest.mark.unit
def test_build_server_allows_non_loopback_with_opt_in_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="prompiler.mcp.server")
    server = build_server(host="0.0.0.0", port=0, allow_non_loopback=True)
    try:
        records = [
            r
            for r in caplog.records
            if r.name == "prompiler.mcp.server" and r.levelno == logging.WARNING
        ]
        assert len(records) == 1
        assert getattr(records[0], "event", None) == "mcp.bind.non_loopback"
        assert getattr(records[0], "host", None) == "0.0.0.0"
    finally:
        server.server_close()


@pytest.mark.integration
def test_healthz_returns_ok(running_server: ThreadingHTTPServer) -> None:
    _host, port = running_server.socket.getsockname()[:2]
    with urllib.request.urlopen(f"http://{LOOPBACK_HOST}:{port}{HEALTHZ_PATH}", timeout=2) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/json"
        assert resp.read() == HEALTHZ_BODY


@pytest.mark.integration
def test_unknown_path_returns_404(running_server: ThreadingHTTPServer) -> None:
    _host, port = running_server.socket.getsockname()[:2]
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"http://{LOOPBACK_HOST}:{port}/nope", timeout=2)
    assert exc_info.value.code == 404
