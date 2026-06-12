"""Failure-mode coverage for runtime orchestrator — PLAN.md L175.

Five scenarios, one ``@pytest.mark.unit`` per scenario:

1. Missing required field → ``ValidationError`` retry path exhausts and
   surfaces as ``ExtractionFailed`` with the originating Pydantic error
   on ``__cause__`` (D5).
2. Type mismatch (string field receives a list) → same retry-then-fail
   path, distinct from the missing-field path because Pydantic emits a
   different ``type`` discriminator inside the chained error.
3. Refusal — adapter raises ``AdapterError`` to model the vendor-level
   policy refusal signal; orchestrator propagates verbatim without
   spending the validation retry budget.
4. Timeout — adapter raises ``asyncio.TimeoutError``; orchestrator does
   not swallow it. The exception class is preserved end-to-end so the
   caller can branch on ``TimeoutError`` instead of generic adapter
   failure.
5. Rate limit — adapter raises ``AdapterError`` after the transport-layer
   retry budget is already exhausted (see ``backends/retry.py``). The
   orchestrator must surface it once, not retry again.

Mirror the harness in ``test_runtime_orchestrator.py``: pytest-asyncio is
not installed, so coroutines run under ``asyncio.run`` inside sync test
functions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Final

import pytest
from pydantic import ValidationError

from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.errors import AdapterError
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_RETRY_BUDGET: Final[int] = 2


def _build_spec() -> EntitySpec:
    return EntitySpec.model_validate(
        {
            "spec_version": 2,
            "name": "doc",
            "task": "extract",
            "fields": [
                {"name": "title", "type": "string", "required": True},
                {"name": "count", "type": "integer", "required": True},
            ],
        }
    )


def _register() -> Registry:
    registry = Registry()
    registry.register("doc", compile_spec(_build_spec()))
    return registry


class _ScriptedAdapter:
    def __init__(self, script: list[dict[str, Any] | AdapterError | asyncio.TimeoutError]) -> None:
        self._script: list[dict[str, Any] | AdapterError | asyncio.TimeoutError] = list(script)
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
        assert self._script, (
            f"_ScriptedAdapter exhausted on call {self.calls}; "
            "test scripted fewer responses than orchestrator requested"
        )
        head = self._script.pop(0)
        if isinstance(head, (AdapterError, asyncio.TimeoutError)):
            raise head
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


@pytest.mark.unit
def test_missing_required_field_exhausts_retry_and_raises_extraction_failed() -> None:
    registry = _register()
    adapter = _ScriptedAdapter([{"title": "ok"}] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any(err["type"] == "missing" for err in cause.errors())


@pytest.mark.unit
def test_type_mismatch_exhausts_retry_and_raises_extraction_failed() -> None:
    registry = _register()
    adapter = _ScriptedAdapter([{"title": ["not", "a", "string"], "count": 1}] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any(err["type"] == "string_type" for err in cause.errors())
    assert not any(err["type"] == "missing" for err in cause.errors())


@pytest.mark.unit
def test_refusal_surfaces_as_adapter_error_without_retry() -> None:
    registry = _register()
    refusal = AdapterError("vendor refused: policy block")
    adapter = _ScriptedAdapter([refusal])

    with pytest.raises(AdapterError) as exc_info:
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert exc_info.value is refusal
    assert adapter.calls == 1


@pytest.mark.unit
def test_timeout_propagates_without_orchestrator_retry() -> None:
    """Pin the timeout contract at the orchestrator layer.

    The orchestrator does not internally wrap the adapter call in
    ``asyncio.wait_for`` — its pipeline is expected to be cancellable so the
    caller can enforce a wall-clock budget via ``wait_for`` at the orchestrator
    boundary. This test wraps the orchestrator's ``run`` coroutine and asserts
    cancellation surfaces as ``asyncio.TimeoutError`` (preserved verbatim, no
    validation-retry double-spend).
    """
    registry = _register()

    class _SleepingAdapter:
        def __init__(self) -> None:
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
            await asyncio.sleep(10)
            return ExtractResult(
                data={"title": "never", "count": 0},
                system_fingerprint=None,
                deterministic=True,
            )

        def supports(self, feature: str) -> bool:
            return feature == "seed"

        def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
            return dict(json_schema)

    adapter = _SleepingAdapter()

    async def _race() -> None:
        await asyncio.wait_for(
            run("doc", "body", backend=adapter, registry=registry),
            timeout=0.05,
        )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_race())

    assert adapter.calls == 1


@pytest.mark.unit
def test_rate_limit_after_adapter_retry_exhaustion_propagates_once() -> None:
    registry = _register()
    exhausted = AdapterError("429 rate limited; retry budget exhausted")
    adapter = _ScriptedAdapter([exhausted])

    with pytest.raises(AdapterError) as exc_info:
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert exc_info.value is exhausted
    assert adapter.calls == 1
