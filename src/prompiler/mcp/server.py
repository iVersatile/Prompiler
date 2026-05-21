"""MCP skeleton HTTP server (P0 L63).

Loopback-bound by default. ``GET /healthz`` returns ``200 {"status":"ok"}``.
Non-loopback bind requires ``allow_non_loopback=True`` and emits a WARN log
line on logger ``prompiler.mcp.server`` with ``event="mcp.bind.non_loopback"``.

Full MCP tool/resource handlers and stdio transport are deferred to P6
(see ``docs/PLAN.md`` §3 L63).
"""

from __future__ import annotations

import ipaddress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from prompiler.obs import get_logger

LOOPBACK_HOST: Final[str] = "127.0.0.1"
HEALTHZ_PATH: Final[str] = "/healthz"
HEALTHZ_BODY: Final[bytes] = b'{"status":"ok"}'

_logger = get_logger("prompiler.mcp.server")


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal ``/healthz`` responder; everything else 404s."""

    server_version = "prompiler-mcp-skeleton/0"

    def do_GET(self) -> None:
        if self.path == HEALTHZ_PATH:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(HEALTHZ_BODY)))
            self.end_headers()
            self.wfile.write(HEALTHZ_BODY)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        _logger.debug(
            format % args,
            extra={"event": "mcp.http.access", "client": self.address_string()},
        )


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}


def build_server(
    host: str = LOOPBACK_HOST,
    port: int = 0,
    *,
    allow_non_loopback: bool = False,
) -> ThreadingHTTPServer:
    """Construct (but do not start) the skeleton HTTP server.

    Raises ``ValueError`` if ``host`` is non-loopback and the opt-in flag is
    not set. Emits a single WARN record when the opt-in is honored.
    """
    if not _is_loopback(host):
        if not allow_non_loopback:
            raise ValueError(
                f"refusing non-loopback bind to {host!r}; pass allow_non_loopback=True to override"
            )
        _logger.warning(
            "binding MCP skeleton to non-loopback host %s",
            host,
            extra={"event": "mcp.bind.non_loopback", "host": host},
        )
    return ThreadingHTTPServer((host, port), HealthHandler)


def serve(
    host: str = LOOPBACK_HOST,
    port: int = 8765,
    *,
    allow_non_loopback: bool = False,
) -> None:
    """Run the skeleton server in the foreground until interrupted."""
    server = build_server(host=host, port=port, allow_non_loopback=allow_non_loopback)
    sockname = server.socket.getsockname()
    _logger.info(
        "mcp skeleton listening on %s:%s",
        sockname[0],
        sockname[1],
        extra={"event": "mcp.serve.start", "host": sockname[0], "port": sockname[1]},
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = [
    "HEALTHZ_BODY",
    "HEALTHZ_PATH",
    "LOOPBACK_HOST",
    "HealthHandler",
    "build_server",
    "serve",
]
