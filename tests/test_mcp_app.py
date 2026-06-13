"""Unit tests for prompiler.mcp.app (P6 — PLAN.md L244-266).

Covers ``build_mcp(registry, backend, *, spec_sources=None, usage_hook=None)``:

- One MCP tool per ``registry.names()``.
- Tool input schema accepts a single ``text: str`` argument.
- Tool output schema matches the spec's compiled Pydantic model.
- ``prompiler://specs/<name>`` returns the raw spec YAML from ``spec_sources``.
- ``prompiler://artefacts/<name>`` returns the compiled prompt + tool schema +
  ``spec_hash`` derived from the bundle.
- Resource handlers reject path-traversal / unknown names (LL-004 / S5).
- A tool call returns structured content matching the model and surfaces
  per-call token usage in the result ``_meta``.

FastMCP's ``list_tools``/``read_resource``/``call_tool`` are async; the repo
keeps tests sync (prod async is reached through sync facades elsewhere), so
each case drives the coroutine through ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import Any, TypeVar

import pytest

from conftest import ScriptedAdapter
from prompiler.backends.base import ExtractResult, ModalContent, StreamEvent
from prompiler.backends.mock import MockAdapter
from prompiler.backends.observability import ObservabilityHook, PricingTable, emit_call_metrics
from prompiler.compiler import compile_spec
from prompiler.eval.runner import CapturingHook
from prompiler.mcp.app import MAX_TEXT_BYTES, build_mcp
from prompiler.runtime.registry import Registry, register_from_dict
from prompiler.spec import EntitySpec

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

RECEIPT_SPEC: dict[str, Any] = {
    "spec_version": 2,
    "name": "receipt",
    "task": "extract",
    "description": "Extract receipt fields.",
    "fields": [
        {"name": "amount", "type": "decimal", "required": True},
    ],
}

INVOICE_YAML = "spec_version: 2\nname: invoice\ntask: extract\n"


def _run(coro: Awaitable[_T]) -> _T:
    return asyncio.run(coro)


def _registry(*specs: dict[str, Any]) -> Registry:
    reg = Registry()
    for spec in specs:
        register_from_dict(spec, registry=reg)
    return reg


@pytest.mark.unit
def test_one_tool_per_registered_spec() -> None:
    reg = _registry(INVOICE_SPEC, RECEIPT_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]))
    tools = _run(mcp.list_tools())
    assert {t.name for t in tools} == {"invoice", "receipt"}


@pytest.mark.unit
def test_tool_input_schema_accepts_text() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]))
    (tool,) = _run(mcp.list_tools())
    props = tool.inputSchema["properties"]
    assert "text" in props
    assert props["text"]["type"] == "string"
    assert tool.inputSchema["required"] == ["text"]


@pytest.mark.unit
def test_tool_output_schema_matches_compiled_model() -> None:
    reg = _registry(INVOICE_SPEC)
    bundle = compile_spec(EntitySpec.model_validate(INVOICE_SPEC))
    mcp = build_mcp(reg, ScriptedAdapter([]))
    (tool,) = _run(mcp.list_tools())
    assert tool.outputSchema == bundle.pydantic_cls.model_json_schema()


@pytest.mark.unit
def test_specs_resource_returns_raw_yaml() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]), spec_sources={"invoice": INVOICE_YAML})
    contents = list(_run(mcp.read_resource("prompiler://specs/invoice")))
    assert contents[0].content == INVOICE_YAML


@pytest.mark.unit
def test_artefacts_resource_returns_prompt_and_schema() -> None:
    reg = _registry(INVOICE_SPEC)
    bundle = compile_spec(EntitySpec.model_validate(INVOICE_SPEC))
    mcp = build_mcp(reg, ScriptedAdapter([]))
    contents = list(_run(mcp.read_resource("prompiler://artefacts/invoice")))
    payload = json.loads(contents[0].content)
    assert payload["prompt"] == bundle.prompt
    assert payload["spec_hash"] == bundle.spec_hash


@pytest.mark.unit
def test_specs_resource_rejects_unknown_name() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]), spec_sources={"invoice": INVOICE_YAML})
    with pytest.raises(Exception):  # noqa: B017 — any miss is acceptable, not a silent hit
        _run(mcp.read_resource("prompiler://specs/nope"))


@pytest.mark.unit
def test_artefacts_resource_rejects_unknown_name() -> None:
    reg = _registry(INVOICE_SPEC)
    mcp = build_mcp(reg, ScriptedAdapter([]))
    with pytest.raises(Exception):  # noqa: B017 — any miss is acceptable, not a silent hit
        _run(mcp.read_resource("prompiler://artefacts/nope"))


@pytest.mark.unit
def test_tool_rejects_oversized_text() -> None:
    reg = _registry(INVOICE_SPEC)
    backend = ScriptedAdapter([{"total": "10.00"}])
    mcp = build_mcp(reg, backend)
    oversized = "x" * (MAX_TEXT_BYTES + 1)
    with pytest.raises(Exception):  # noqa: B017 — FastMCP wraps the ValueError
        _run(mcp.call_tool("invoice", {"text": oversized}))


@pytest.mark.unit
def test_tool_accepts_text_at_size_limit() -> None:
    reg = _registry(INVOICE_SPEC)
    backend = ScriptedAdapter([{"total": "10.00"}])
    mcp = build_mcp(reg, backend)
    at_limit = "x" * MAX_TEXT_BYTES
    result = _run(mcp.call_tool("invoice", {"text": at_limit}))
    assert result.structuredContent == {"total": "10.00"}


@pytest.mark.unit
def test_tool_call_returns_structured_content_and_usage_meta() -> None:
    reg = _registry(INVOICE_SPEC)
    hook = CapturingHook()
    backend = ScriptedAdapter(
        [{"total": "10.00"}],
        observability=hook,
        tokens=[(7, 3)],
    )
    mcp = build_mcp(reg, backend, usage_hook=hook)
    result = _run(mcp.call_tool("invoice", {"text": "Invoice total 10.00"}))
    assert result.structuredContent == {"total": "10.00"}
    usage = result.meta["usage"]
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 3


_TITLE_SPEC: dict[str, Any] = {
    "spec_version": 2,
    "name": "doc",
    "task": "extract",
    "fields": [{"name": "title", "type": "string", "required": True}],
}


@pytest.mark.unit
def test_tool_call_threads_image_modal_parts_to_mock_adapter() -> None:
    reg = _registry(_TITLE_SPEC)
    mcp = build_mcp(reg, MockAdapter())
    part = {
        "modality": "image",
        "media_type": "image/png",
        "data": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii"),
    }
    result = _run(mcp.call_tool("doc", {"text": "body", "modal_parts": [part]}))
    assert result.structuredContent == {"title": "mock"}


@pytest.mark.unit
def test_tool_call_propagates_modal_capability_error() -> None:
    # MockAdapter raises CapabilityError on audio; this only fires if the tool
    # actually threads modal_parts into the adapter. A dropped arg would let the
    # call succeed, so this asserts real end-to-end modal threading.
    reg = _registry(_TITLE_SPEC)
    mcp = build_mcp(reg, MockAdapter())
    part = {
        "modality": "audio",
        "media_type": "audio/wav",
        "data": base64.b64encode(b"RIFFfake").decode("ascii"),
    }
    with pytest.raises(Exception):  # noqa: B017 — FastMCP wraps CapabilityError
        _run(mcp.call_tool("doc", {"text": "body", "modal_parts": [part]}))


class _StreamingScriptedAdapter:
    """Streaming backend adapter for MCP streaming-surface tests (H2).

    ``stream_extract`` streams one non-terminal ``StreamEvent`` per delta then a
    terminal ``StreamEvent`` carrying ``ExtractResult(data=payload)``. When an
    ``ObservabilityHook`` is supplied it emits ``BackendCallMetrics`` after the
    stream drains (mirroring the real SSE adapters), so the MCP usage ``_meta``
    has a call to drain. ``calls`` counts ``stream_extract`` invocations — a zero
    count proves the ``MAX_TEXT_BYTES`` guard rejected oversized input pre-call.
    """

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        deltas: Sequence[str] = (),
        observability: ObservabilityHook | None = None,
        tokens: tuple[int, int] = (0, 0),
        pricing: PricingTable | None = None,
        backend_name: str = "scripted-stream",
        model: str = "test-stream-model",
    ) -> None:
        self._payload = payload
        self._deltas = tuple(deltas)
        self._observability = observability
        self._tokens = tokens
        self._pricing = pricing if pricing is not None else PricingTable()
        self._backend_name = backend_name
        self._model = model
        self.calls: int = 0

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
        self.calls += 1
        for delta in self._deltas:
            yield StreamEvent(delta=delta)
        if self._observability is not None:
            prompt_tokens, completion_tokens = self._tokens
            await emit_call_metrics(
                hook=self._observability,
                backend=self._backend_name,
                model=self._model,
                latency_seconds=0.0,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                pricing=self._pricing,
            )
        yield StreamEvent(
            delta="",
            result=ExtractResult(data=self._payload, system_fingerprint=None, deterministic=True),
        )

    def supports(self, feature: str) -> bool:
        return feature in ("seed", "streaming")

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


@pytest.mark.unit
def test_streaming_tool_call_returns_structured_content_and_usage_meta() -> None:
    reg = _registry(INVOICE_SPEC)
    hook = CapturingHook()
    backend = _StreamingScriptedAdapter(
        {"total": "10.00"},
        deltas=['{"total":', ' "10.00"}'],
        observability=hook,
        tokens=(7, 3),
    )
    mcp = build_mcp(reg, backend, usage_hook=hook, stream=True)
    result = _run(mcp.call_tool("invoice", {"text": "Invoice total 10.00"}))
    assert backend.calls == 1
    assert result.structuredContent == {"total": "10.00"}
    usage = result.meta["usage"]
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 3


@pytest.mark.unit
def test_streaming_tool_rejects_oversized_text() -> None:
    reg = _registry(INVOICE_SPEC)
    backend = _StreamingScriptedAdapter({"total": "10.00"})
    mcp = build_mcp(reg, backend, stream=True)
    oversized = "x" * (MAX_TEXT_BYTES + 1)
    with pytest.raises(Exception):  # noqa: B017 — FastMCP wraps the ValueError
        _run(mcp.call_tool("invoice", {"text": oversized}))
    assert backend.calls == 0  # guard fired before any streaming call
