"""Module entry point for the MCP skeleton server.

Lets ``python -m prompiler.mcp`` launch :func:`prompiler.mcp.server.serve`
without going through the CLI. This is what the production Dockerfile's
``CMD`` invokes (see ``Dockerfile``); it is also the cleanest path for ad-hoc
local smoke runs via ``uv run python -m prompiler.mcp``.

Configuration is environment-driven so the container image stays generic:

- ``PROMPILER_MCP_HOST`` (default ``127.0.0.1``).
- ``PROMPILER_MCP_PORT`` (default ``8765``).
- ``PROMPILER_MCP_ALLOW_NON_LOOPBACK`` (truthy = opt-in to non-loopback bind).

Defaults stay loopback-only; container deployments override
``PROMPILER_MCP_HOST=0.0.0.0`` and ``PROMPILER_MCP_ALLOW_NON_LOOPBACK=1``
explicitly, which is the same safety contract enforced by
``build_server`` (see ``docs/RULES.md`` §8).
"""

from __future__ import annotations

import os
import sys

from prompiler.mcp.server import LOOPBACK_HOST, serve
from prompiler.obs import configure_logging

_TRUTHY = {"1", "true", "yes", "on"}


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


def main() -> int:
    configure_logging()
    host = os.environ.get("PROMPILER_MCP_HOST", LOOPBACK_HOST)
    port = _env_int("PROMPILER_MCP_PORT", 8765)
    allow_non_loopback = _env_bool("PROMPILER_MCP_ALLOW_NON_LOOPBACK", default=False)
    try:
        serve(host=host, port=port, allow_non_loopback=allow_non_loopback)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
