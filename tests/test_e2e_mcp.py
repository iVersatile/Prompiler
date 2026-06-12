"""End-to-end MCP protocol tests (P6 — PLAN.md L244-266, closes gap G4).

The P4 backlog gap G4 asked for an integration test that drives an extract
*over the MCP protocol* end-to-end — not just the P0 ``/healthz`` + 404 probe.
These tests wire a real ``build_mcp(...)`` server to an in-memory
``ClientSession`` via ``create_connected_server_and_client_session`` and exercise
the full client handshake -> ``initialize`` -> ``list_tools`` -> ``call_tool`` ->
``read_resource`` round-trip. The transport is in-memory object streams, so the
same protocol code path that stdio / streamable-http use is covered without
opening a socket.

The repo keeps tests synchronous; each case drives the async client session
through ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, TypeVar

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from conftest import ScriptedAdapter
from prompiler.eval.runner import CapturingHook
from prompiler.mcp.app import build_mcp
from prompiler.runtime.registry import Registry, register_from_dict

_T = TypeVar("_T")

INVOICE_SPEC: dict[str, Any] = {
    "spec_version": 2,
    "name": "invoice",
    "task": "extract",
    "description": "Extract invoice fields.",
    "fields": [
        {"name": "total", "type": "decimal", "required": True},
    ],
}

INVOICE_YAML = "spec_version: 1\nname: invoice\ntask: extract\n"


def _run(coro: Awaitable[_T]) -> _T:
    return asyncio.run(coro)


def _registry(*specs: dict[str, Any]) -> Registry:
    reg = Registry()
    for spec in specs:
        register_from_dict(spec, registry=reg)
    return reg


@pytest.mark.e2e
def test_client_discovers_tool_over_protocol() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]))

    async def _drive() -> set[str]:
        async with create_connected_server_and_client_session(mcp) as session:
            await session.initialize()
            result = await session.list_tools()
            return {tool.name for tool in result.tools}

    assert _run(_drive()) == {"invoice"}


@pytest.mark.e2e
def test_extract_over_protocol_returns_structured_content_and_usage() -> None:
    reg = _registry(INVOICE_SPEC)
    hook = CapturingHook()
    backend = ScriptedAdapter([{"total": "10.00"}], observability=hook, tokens=[(7, 3)])
    mcp = build_mcp(reg, backend, usage_hook=hook)

    async def _drive() -> Any:
        async with create_connected_server_and_client_session(mcp) as session:
            await session.initialize()
            return await session.call_tool("invoice", {"text": "Invoice total 10.00"})

    result = _run(_drive())
    assert result.isError is False
    assert result.structuredContent == {"total": "10.00"}
    usage = result.meta["usage"]
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 3


@pytest.mark.e2e
def test_spec_resource_read_over_protocol() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]), spec_sources={"invoice": INVOICE_YAML})

    async def _drive() -> str:
        async with create_connected_server_and_client_session(mcp) as session:
            await session.initialize()
            result = await session.read_resource("prompiler://specs/invoice")
            return result.contents[0].text  # type: ignore[union-attr]

    assert _run(_drive()) == INVOICE_YAML


@pytest.mark.e2e
def test_artefact_resource_read_over_protocol() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]))

    async def _drive() -> dict[str, Any]:
        async with create_connected_server_and_client_session(mcp) as session:
            await session.initialize()
            result = await session.read_resource("prompiler://artefacts/invoice")
            return json.loads(result.contents[0].text)  # type: ignore[union-attr]

    payload = _run(_drive())
    assert "prompt" in payload
    assert "spec_hash" in payload
