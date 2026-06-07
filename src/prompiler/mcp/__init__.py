"""MCP integration surface for prompiler.

``server`` keeps the P0 loopback ``/healthz`` skeleton. P6 adds ``app`` —
``build_mcp`` wires every registered spec into a FastMCP server (one tool per
spec plus ``prompiler://specs`` and ``prompiler://artefacts`` resources).
"""

from prompiler.mcp.app import build_mcp

__all__ = ["build_mcp"]
