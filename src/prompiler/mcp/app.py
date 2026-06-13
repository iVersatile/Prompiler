"""FastMCP application builder for prompiler (P6 — PLAN.md L244-266).

``build_mcp(registry, backend, *, spec_sources=None, usage_hook=None)`` wires
every registered spec into one MCP tool plus two resource families:

- ``prompiler://specs/<name>``     — raw spec YAML (from ``spec_sources``).
- ``prompiler://artefacts/<name>`` — compiled prompt + tool schema + spec_hash.

Resource handlers raise ``KeyError`` on an unknown name; combined with the
registry's ``^[a-z0-9_-]+$`` key pattern this closes path-traversal (LL-004 /
S5) — a traversal name can never resolve to a registered bundle.

Each tool call runs the orchestrator, returns structured content matching the
spec's compiled Pydantic model, and surfaces per-call token usage in the
result ``_meta`` (drained from a shared ``CapturingHook``).

Tool input ``text`` is bounded by ``MAX_TEXT_BYTES`` (P6 DoD — "no unbounded
payload sizes"); an oversized request is rejected before reaching a backend.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from prompiler.backends.base import ModalContent
from prompiler.backends.observability import BackendCallMetrics
from prompiler.eval.runner import CapturingHook
from prompiler.runtime import orchestrator
from prompiler.runtime.registry import Registry

_SPEC_MIME = "application/yaml"
_ARTEFACT_MIME = "application/json"

# Bound the per-call tool input so a single request can't pin memory or stream
# an unbounded prompt to a backend (P6 DoD — "no unbounded payload sizes").
MAX_TEXT_BYTES = 1_048_576  # 1 MiB of UTF-8 source text


def _usage_meta(metric: BackendCallMetrics) -> dict[str, Any]:
    return {
        "usage": {
            "backend": metric.backend,
            "model": metric.model,
            "prompt_tokens": metric.prompt_tokens,
            "completion_tokens": metric.completion_tokens,
            "cost_usd": metric.cost_usd,
        }
    }


def _decode_modal_parts(raw: list[dict[str, Any]] | None) -> tuple[ModalContent, ...]:
    """Rebuild ``ModalContent`` parts from the JSON-over-the-wire tool input.

    Each inbound dict carries ``data`` as base64 text (JSON can't hold raw
    bytes); we decode it back to the ``bytes`` that ``ModalContent`` expects so
    the adapter receives the same payload an in-process caller would pass.
    """
    if not raw:
        return ()
    return tuple(
        ModalContent(
            modality=p["modality"],
            media_type=p["media_type"],
            data=base64.b64decode(p["data"]),
        )
        for p in raw
    )


def build_mcp(
    registry: Registry,
    backend: Any,
    *,
    spec_sources: Mapping[str, str] | None = None,
    usage_hook: CapturingHook | None = None,
    name: str = "prompiler",
    stream: bool = False,
) -> FastMCP:
    """Build a FastMCP server exposing every spec in ``registry`` as a tool.

    With ``stream=True`` each tool drives ``orchestrator.run_stream`` and folds
    the streamed deltas down to the terminal validated model — the MCP tool
    still returns one ``CallToolResult``, so streaming is an internal detail
    that preserves the same result shape, ``MAX_TEXT_BYTES`` guard, and usage
    ``_meta`` drain as the buffered path (H2).
    """
    mcp = FastMCP(name)
    sources: Mapping[str, str] = spec_sources or {}
    drain_lock = asyncio.Lock()

    for spec_name in sorted(registry.names()):
        bundle = registry.get(spec_name)
        _register_tool(mcp, spec_name, bundle, backend, registry, usage_hook, drain_lock, stream)

    @mcp.resource("prompiler://specs/{spec_name}", mime_type=_SPEC_MIME)
    def _spec_resource(spec_name: str) -> str:
        try:
            return sources[spec_name]
        except KeyError as err:
            raise KeyError(f"no spec source for {spec_name!r}") from err

    @mcp.resource("prompiler://artefacts/{spec_name}", mime_type=_ARTEFACT_MIME)
    def _artefact_resource(spec_name: str) -> str:
        try:
            bundle = registry.get(spec_name)
        except KeyError as err:
            raise KeyError(f"no artefact bundle for {spec_name!r}") from err
        return json.dumps(
            {
                "prompt": bundle.prompt,
                "tool_schema": dict(bundle.tool_schema_per_backend),
                "spec_hash": bundle.spec_hash,
            }
        )

    return mcp


async def _drain_stream(
    spec_name: str,
    text: str,
    backend: Any,
    registry: Registry,
    modal_parts: tuple[ModalContent, ...],
) -> BaseModel:
    """Drain ``run_stream`` to its terminal validated model.

    ``run_stream`` yields non-terminal ``StreamEvent`` deltas then one validated
    ``BaseModel`` (and raises ``ExtractionFailed`` if no terminal event arrives).
    The MCP tool returns a single result, so the deltas are discarded and only
    the model is kept.
    """
    model: BaseModel | None = None
    async for item in orchestrator.run_stream(
        spec_name,
        text,
        backend=backend,
        registry=registry,
        modal_parts=modal_parts,
    ):
        if isinstance(item, BaseModel):
            model = item
    if model is None:  # pragma: no cover — run_stream raises before this
        raise RuntimeError(f"stream for {spec_name!r} produced no terminal model")
    return model


def _register_tool(
    mcp: FastMCP,
    spec_name: str,
    bundle: Any,
    backend: Any,
    registry: Registry,
    usage_hook: CapturingHook | None,
    drain_lock: asyncio.Lock,
    stream: bool,
) -> None:
    model_cls = bundle.pydantic_cls

    async def _tool(text: str, modal_parts: list[dict[str, Any]] | None = None) -> Any:
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"text exceeds {MAX_TEXT_BYTES} bytes; split the input before calling")
        before = len(usage_hook.calls) if usage_hook is not None else 0
        decoded = _decode_modal_parts(modal_parts)
        if stream:
            model = await _drain_stream(spec_name, text, backend, registry, decoded)
        else:
            model = await orchestrator.run(
                spec_name,
                text,
                backend=backend,
                registry=registry,
                modal_parts=decoded,
            )
        structured = model.model_dump(mode="json")
        meta: dict[str, Any] | None = None
        if usage_hook is not None:
            async with drain_lock:
                new_calls = usage_hook.calls[before:]
            if new_calls:
                meta = _usage_meta(new_calls[-1])
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(structured))],
            structuredContent=structured,
            _meta=meta,
        )

    _tool.__annotations__ = {
        "text": str,
        "modal_parts": list[dict[str, Any]],
        "return": model_cls,
    }
    mcp.add_tool(
        _tool,
        name=spec_name,
        description=f"Extract structured fields for spec {spec_name!r}.",
        structured_output=True,
    )
