"""Integration test: backend swap — orchestrator is adapter-agnostic.

Distinct angle from the other integration tests: those scripts validate
*shape* and *retry* behaviour against a single adapter. This file plugs the
same compiled spec into two *structurally different* scripted adapters and
proves the orchestrator returns equivalent typed instances regardless of
which adapter satisfies the ``Backend`` protocol.

- adapter A: dict-returning, eager (returns the next scripted payload),
- adapter B: dict-returning via a lazy generator (consumes payloads one at a
  time from an internal iterator) — same external contract, different impl,
- both adapters expose ``extract(*, prompt, json_schema, timeout=None)`` and
  ``to_tool_schema(json_schema)``.

Adapters are fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from typing import Any, Final

import pytest
from pydantic import BaseModel

from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.compiler import compile_spec
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_NOTE_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "short_note",
    "task": "extract",
    "fields": [
        {"name": "title", "type": "string", "required": True},
        {"name": "body", "type": "string", "required": True},
    ],
}

_PAYLOAD_ONE: Final[dict[str, Any]] = {
    "title": "Outage post-mortem",
    "body": "Root cause identified in deploy 8f2a1c.",
}

_PAYLOAD_TWO: Final[dict[str, Any]] = {
    "title": "Release notes",
    "body": "Patched cache invalidation race.",
}


class _EagerScriptedAdapter:
    """Pops payloads from a list head — eager, list-backed."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script: list[dict[str, Any]] = list(script)
        self.calls: int = 0

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> ExtractResult:
        self.calls += 1
        return ExtractResult(data=self._script.pop(0), system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


class _LazyScriptedAdapter:
    """Pulls payloads from an iterator — lazy, generator-backed."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._iter: Iterator[dict[str, Any]] = iter(script)
        self.calls: int = 0

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> ExtractResult:
        self.calls += 1
        return ExtractResult(data=next(self._iter), system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register() -> Registry:
    registry = Registry()
    registry.register("short_note", compile_spec(EntitySpec.model_validate(_NOTE_SPEC)))
    return registry


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()


@pytest.mark.integration
def test_both_adapters_return_equivalent_typed_instances() -> None:
    registry = _register()
    eager = _EagerScriptedAdapter([_PAYLOAD_ONE])
    lazy = _LazyScriptedAdapter([_PAYLOAD_ONE])

    result_eager = asyncio.run(run("short_note", "note body", backend=eager, registry=registry))
    result_lazy = asyncio.run(run("short_note", "note body", backend=lazy, registry=registry))

    assert isinstance(result_eager, BaseModel)
    assert isinstance(result_lazy, BaseModel)
    assert type(result_eager) is type(result_lazy)
    assert _dump(result_eager) == _dump(result_lazy) == _PAYLOAD_ONE
    assert eager.calls == 1
    assert lazy.calls == 1


@pytest.mark.integration
def test_per_adapter_call_counts_are_independent() -> None:
    registry = _register()
    eager = _EagerScriptedAdapter([_PAYLOAD_ONE])
    lazy = _LazyScriptedAdapter([_PAYLOAD_TWO])

    asyncio.run(run("short_note", "one", backend=eager, registry=registry))

    assert eager.calls == 1
    assert lazy.calls == 0

    asyncio.run(run("short_note", "two", backend=lazy, registry=registry))

    assert eager.calls == 1
    assert lazy.calls == 1


@pytest.mark.integration
def test_swapping_adapter_mid_batch_does_not_leak_state() -> None:
    registry = _register()
    eager = _EagerScriptedAdapter([_PAYLOAD_ONE])
    lazy = _LazyScriptedAdapter([_PAYLOAD_TWO])

    first = asyncio.run(run("short_note", "one", backend=eager, registry=registry))
    second = asyncio.run(run("short_note", "two", backend=lazy, registry=registry))

    assert isinstance(first, BaseModel)
    assert isinstance(second, BaseModel)
    assert _dump(first) == _PAYLOAD_ONE
    assert _dump(second) == _PAYLOAD_TWO
    assert first.title != second.title
    assert eager.calls == 1
    assert lazy.calls == 1
