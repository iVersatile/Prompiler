"""Module entry point for the MCP server (P6 — PLAN.md L244-254).

Lets ``python -m prompiler.mcp`` launch a FastMCP server without going through
the CLI. This is what the production Dockerfile's ``CMD`` invokes (see
``Dockerfile``); it is also the cleanest path for ad-hoc local smoke runs via
``uv run python -m prompiler.mcp``.

Transports:

- ``stdio`` (default) — what Claude Desktop / MCP Inspector use.
- ``http`` (``--transport http --port N``) — maps to FastMCP's
  ``streamable-http``; binds ``127.0.0.1`` by default. A ``/healthz`` route is
  mounted only on the HTTP transport (stdio has no socket to probe).

Configuration is environment-driven so the container image stays generic:

- ``PROMPILER_MCP_BACKEND`` (default ``ollama``) — single backend selected at
  startup (option A); one of ``claude|openai|gemini|ollama``.
- ``PROMPILER_MCP_EXAMPLES_DIR`` (default: repo ``examples/``) — spec YAMLs to
  compile and expose as tools.
- ``PROMPILER_MCP_HOST`` (default ``127.0.0.1``).
- ``PROMPILER_MCP_PORT`` (default ``8765``); ``--port`` overrides it.
- ``PROMPILER_MCP_ALLOW_NON_LOOPBACK`` (truthy = opt-in to non-loopback bind).

Defaults stay loopback-only; non-loopback binds require an explicit opt-in,
the same safety contract enforced for the P0 skeleton (see ``docs/RULES.md``
§8).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from starlette.responses import Response

from prompiler.mcp.app import build_mcp
from prompiler.mcp.runtime import build_backend, load_examples
from prompiler.mcp.server import (
    HEALTHZ_BODY,
    HEALTHZ_PATH,
    LOOPBACK_HOST,
    _is_loopback,
)
from prompiler.obs import configure_logging

_TRUTHY = {"1", "true", "yes", "on"}

_DEFAULT_BACKEND = "ollama"
_DEFAULT_PORT = 8765


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(f"prompiler.mcp: invalid {name}={raw!r}; expected integer\n")
        raise SystemExit(2) from None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="prompiler.mcp")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=None)
    return parser.parse_args(argv)


def _resolve_transport(cli: str) -> str:
    return "stdio" if cli == "stdio" else "streamable-http"


def _resolve_backend_name() -> str:
    return os.environ.get("PROMPILER_MCP_BACKEND", _DEFAULT_BACKEND)


def _resolve_examples_dir() -> Path:
    raw = os.environ.get("PROMPILER_MCP_EXAMPLES_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3] / "examples"


def _mount_healthz(mcp: object) -> None:
    @mcp.custom_route(HEALTHZ_PATH, methods=["GET"])  # type: ignore[attr-defined, untyped-decorator]
    async def _healthz(_request: object) -> Response:
        return Response(HEALTHZ_BODY, media_type="application/json")


def _serve_mcp(
    mcp: object,
    *,
    transport: str,
    host: str,
    port: int,
    allow_non_loopback: bool,
) -> None:
    if transport == "streamable-http":
        if not _is_loopback(host) and not allow_non_loopback:
            raise ValueError(
                f"refusing to bind MCP HTTP transport to non-loopback host {host!r}; "
                "set PROMPILER_MCP_ALLOW_NON_LOOPBACK=1 to override"
            )
        mcp.settings.host = host  # type: ignore[attr-defined]
        mcp.settings.port = port  # type: ignore[attr-defined]
        _mount_healthz(mcp)
    mcp.run(transport)  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    transport = _resolve_transport(args.transport)
    host = os.environ.get("PROMPILER_MCP_HOST", LOOPBACK_HOST)
    port = args.port if args.port is not None else _env_int("PROMPILER_MCP_PORT", _DEFAULT_PORT)
    allow_non_loopback = _env_bool("PROMPILER_MCP_ALLOW_NON_LOOPBACK", default=False)
    backend, hook = build_backend(_resolve_backend_name())
    registry, sources = load_examples(_resolve_examples_dir())
    mcp = build_mcp(registry, backend, spec_sources=sources, usage_hook=hook)
    try:
        _serve_mcp(
            mcp,
            transport=transport,
            host=host,
            port=port,
            allow_non_loopback=allow_non_loopback,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
