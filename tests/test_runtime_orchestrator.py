"""Contract tests for runtime orchestrator — PLAN.md L160-L162.

Covers the fat 4b unit:

- L160: ``run()`` / ``run_sync()`` async + sync surface.
- L161: ``run_batch()`` with ``asyncio.Semaphore`` + per-item isolation.
- L162: doc-size guardrail via ``ArtefactBundle.max_input_tokens``.

Pinned design decisions (carried from PLAN review):

- D4: prompt assembly appends a literal ``## Input\\n`` block. The 2nd-
  attempt prompt prepends a corrective feedback marker.
- D5: validation exhaustion raises ``ExtractionFailed`` chained from the
  originating ``pydantic.ValidationError`` via ``__cause__``.

pytest-asyncio is not installed; coroutines are driven via
``asyncio.run`` inside sync test functions so the file runs under the
default pytest configuration.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from prompiler.backends.base import CapabilityError, ExtractResult, ModalContent
from prompiler.backends.mock import MockAdapter
from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.errors import AdapterError
from prompiler.runtime.orchestrator import (
    result_cache_info,
    run,
    run_batch,
    run_sync,
)
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec


def _build_simple_spec(name: str, *, max_input_tokens: int | None = None) -> EntitySpec:
    return EntitySpec.model_validate(
        {
            "spec_version": 2,
            "name": name,
            "task": "extract",
            "fields": [
                {"name": "title", "type": "string", "required": True},
            ],
            "max_input_tokens": max_input_tokens,
        }
    )


def _register(name: str, *, max_input_tokens: int | None = None) -> Registry:
    registry = Registry()
    bundle = compile_spec(_build_simple_spec(name, max_input_tokens=max_input_tokens))
    registry.register(name, bundle)
    return registry


class _ScriptedAdapter:
    """Adapter that replays a scripted queue of dict responses or exceptions.

    Each ``extract`` call pops the head of the queue. A dict is returned
    as the adapter response; an exception is raised. Prompts are captured
    in ``prompts`` so tests can assert on assembly.
    """

    def __init__(self, script: list[dict[str, Any] | Exception]) -> None:
        self._script: list[dict[str, Any] | Exception] = list(script)
        self.prompts: list[str] = []
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
        self.prompts.append(prompt)
        head = self._script.pop(0)
        if isinstance(head, Exception):
            raise head
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


class _CountingAdapter:
    """Adapter that tracks peak in-flight concurrency under asyncio."""

    def __init__(self, *, delay: float = 0.01) -> None:
        self._lock = asyncio.Lock()
        self._in_flight = 0
        self.peak = 0
        self._delay = delay
        self.calls = 0

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
        async with self._lock:
            self._in_flight += 1
            if self._in_flight > self.peak:
                self.peak = self._in_flight
            self.calls += 1
        try:
            await asyncio.sleep(self._delay)
            return ExtractResult(data={"title": "ok"}, system_fingerprint=None, deterministic=True)
        finally:
            async with self._lock:
                self._in_flight -= 1

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


@pytest.mark.unit
def test_run_returns_validated_pydantic_instance_on_first_try() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "hello"}])

    result = asyncio.run(run("doc", "the document body", backend=adapter, registry=registry))

    assert isinstance(result, BaseModel)
    assert result.title == "hello"  # type: ignore[attr-defined]
    assert adapter.calls == 1


@pytest.mark.unit
def test_run_appends_input_block_to_prompt() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "hello"}])

    asyncio.run(run("doc", "the document body", backend=adapter, registry=registry))

    sent = adapter.prompts[0]
    assert "## Input\n" in sent
    assert sent.endswith("the document body")


@pytest.mark.unit
def test_run_raises_keyerror_for_unknown_name() -> None:
    registry = Registry()
    adapter = _ScriptedAdapter([{"title": "x"}])

    with pytest.raises(KeyError):
        asyncio.run(run("missing", "text", backend=adapter, registry=registry))

    assert adapter.calls == 0


@pytest.mark.unit
def test_run_retries_once_then_raises_extraction_failed_with_validation_cause() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"wrong_key": "x"}, {"wrong_key": "y"}])

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert isinstance(exc_info.value.__cause__, ValidationError)


@pytest.mark.unit
def test_run_second_attempt_prepends_corrective_feedback() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"wrong_key": "x"}, {"title": "ok"}])

    result = asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert isinstance(result, BaseModel)
    assert adapter.calls == 2
    second = adapter.prompts[1]
    assert "Previous attempt failed validation" in second


@pytest.mark.unit
def test_run_propagates_adapter_error_without_retry() -> None:
    registry = _register("doc")
    boom = AdapterError("vendor down")
    adapter = _ScriptedAdapter([boom])

    with pytest.raises(AdapterError) as exc_info:
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert exc_info.value is boom
    assert adapter.calls == 1


@pytest.mark.unit
def test_run_enforces_doc_size_guardrail_before_adapter_call() -> None:
    registry = _register("doc", max_input_tokens=4)
    adapter = _ScriptedAdapter([{"title": "never reached"}])

    with pytest.raises(ExtractionFailed):
        asyncio.run(run("doc", "x" * 1000, backend=adapter, registry=registry))

    assert adapter.calls == 0


@pytest.mark.unit
def test_run_skips_guardrail_when_max_input_tokens_is_none() -> None:
    registry = _register("doc", max_input_tokens=None)
    adapter = _ScriptedAdapter([{"title": "ok"}])

    result = asyncio.run(run("doc", "x" * 10_000, backend=adapter, registry=registry))

    assert isinstance(result, BaseModel)
    assert adapter.calls == 1


@pytest.mark.unit
def test_run_sync_drives_async_run_under_asyncio_run() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "hello"}])

    result = run_sync("doc", "body", backend=adapter, registry=registry)

    assert isinstance(result, BaseModel)
    assert result.title == "hello"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_run_batch_returns_one_result_per_input_in_order() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "a"}, {"title": "b"}, {"title": "c"}])

    results = asyncio.run(
        run_batch("doc", ["t1", "t2", "t3"], backend=adapter, registry=registry, concurrency=2)
    )

    assert len(results) == 3
    titles = [r.title for r in results if isinstance(r, BaseModel)]  # type: ignore[attr-defined]
    assert titles == ["a", "b", "c"]


@pytest.mark.unit
def test_run_batch_caps_in_flight_at_semaphore_concurrency() -> None:
    registry = _register("doc")
    adapter = _CountingAdapter(delay=0.02)

    results = asyncio.run(
        run_batch(
            "doc",
            ["t1", "t2", "t3", "t4", "t5", "t6"],
            backend=adapter,
            registry=registry,
            concurrency=2,
        )
    )

    assert len(results) == 6
    assert adapter.calls == 6
    assert adapter.peak <= 2


@pytest.mark.unit
def test_run_batch_isolates_per_item_failures() -> None:
    registry = _register("doc")
    boom = AdapterError("middle item exploded")
    adapter = _ScriptedAdapter([{"title": "a"}, boom, {"title": "c"}])

    results = asyncio.run(
        run_batch("doc", ["t1", "t2", "t3"], backend=adapter, registry=registry, concurrency=1)
    )

    assert len(results) == 3
    assert isinstance(results[0], BaseModel)
    assert isinstance(results[1], AdapterError)
    assert results[1] is boom
    assert isinstance(results[2], BaseModel)


@pytest.mark.unit
def test_run_threads_modal_parts_to_mock_adapter() -> None:
    registry = _register("doc")
    image = ModalContent(modality="image", media_type="image/png", data=b"\x89PNG\r\n\x1a\nfake")

    result = asyncio.run(
        run("doc", "body", backend=MockAdapter(), registry=registry, modal_parts=[image])
    )

    assert isinstance(result, BaseModel)
    assert result.title == "mock"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_run_propagates_capability_error_for_unsupported_modality() -> None:
    registry = _register("doc")
    audio = ModalContent(modality="audio", media_type="audio/wav", data=b"RIFFfake")

    with pytest.raises(CapabilityError):
        asyncio.run(
            run("doc", "body", backend=MockAdapter(), registry=registry, modal_parts=[audio])
        )


class _OtherScriptedAdapter(_ScriptedAdapter):
    """Distinct adapter subclass so the backend-identity cache dimension differs."""


@pytest.mark.unit
def test_result_cache_serves_repeated_identical_extract() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "only"}])

    first = asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    second = asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == 1
    assert first.title == "only"  # type: ignore[attr-defined]
    assert second.title == "only"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_result_cache_misses_on_input_text_change() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "a"}, {"title": "b"}])

    asyncio.run(run("doc", "first body", backend=adapter, registry=registry))
    asyncio.run(run("doc", "second body", backend=adapter, registry=registry))

    assert adapter.calls == 2


@pytest.mark.unit
def test_result_cache_misses_on_spec_change() -> None:
    reg_a = _register("doc_a")
    first_adapter = _ScriptedAdapter([{"title": "one"}])
    asyncio.run(run("doc_a", "body", backend=first_adapter, registry=reg_a))

    reg_b = _register("doc_b")
    second_adapter = _ScriptedAdapter([{"title": "two"}])
    result = asyncio.run(run("doc_b", "body", backend=second_adapter, registry=reg_b))

    assert second_adapter.calls == 1
    assert result.title == "two"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_result_cache_misses_on_backend_class_change() -> None:
    registry = _register("doc")
    first_adapter = _ScriptedAdapter([{"title": "one"}])
    asyncio.run(run("doc", "body", backend=first_adapter, registry=registry))

    other_adapter = _OtherScriptedAdapter([{"title": "two"}])
    result = asyncio.run(run("doc", "body", backend=other_adapter, registry=registry))

    assert other_adapter.calls == 1
    assert result.title == "two"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_result_cache_misses_on_model_change() -> None:
    registry = _register("doc")
    first_adapter = _ScriptedAdapter([{"title": "one"}])
    first_adapter._model = "model-a"  # type: ignore[attr-defined]
    asyncio.run(run("doc", "body", backend=first_adapter, registry=registry))

    second_adapter = _ScriptedAdapter([{"title": "two"}])
    second_adapter._model = "model-b"  # type: ignore[attr-defined]
    result = asyncio.run(run("doc", "body", backend=second_adapter, registry=registry))

    assert second_adapter.calls == 1
    assert result.title == "two"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_result_cache_misses_on_prompt_change_at_constant_spec_hash() -> None:
    """B2: the cache key hashes ``bundle.prompt``, not only ``spec_hash``.

    The refine loop patches the prompt via ``dataclasses.replace`` while the
    spec — and therefore ``spec_hash`` — stays constant. Without the
    ``prompt_hash`` dimension the second run would serve a stale, pre-refine
    extraction. Holding spec/backend/text fixed and varying only the prompt
    must MISS.
    """
    spec = _build_simple_spec("doc")
    bundle = compile_spec(spec)
    adapter = _ScriptedAdapter([{"title": "one"}, {"title": "two"}])

    reg_a = Registry()
    reg_a.register("doc", bundle)
    asyncio.run(run("doc", "body", backend=adapter, registry=reg_a))

    patched = dataclasses.replace(bundle, prompt=bundle.prompt + "\n\n## Refinement\nBe precise.")
    reg_b = Registry()
    reg_b.register("doc", patched)
    second = asyncio.run(run("doc", "body", backend=adapter, registry=reg_b))

    assert patched.spec_hash == bundle.spec_hash
    assert adapter.calls == 2
    assert second.title == "two"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_result_cache_info_tracks_hits_misses_and_size() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "x"}])

    asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    info = result_cache_info()
    assert info.misses == 1
    assert info.hits == 1
    assert info.currsize == 1


# --- B3: cache invalidation + opt-out (PLAN.md L147-150) ----------------------


@pytest.mark.unit
def test_result_cache_invalidated_by_protocol_version_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A COMPILER_PROTOCOL_VERSION bump changes spec_hash -> stale entries miss."""
    spec = _build_simple_spec("doc")

    reg_v1 = Registry()
    reg_v1.register("doc", compile_spec(spec))
    adapter = _ScriptedAdapter([{"title": "v1"}, {"title": "v2"}])
    first = asyncio.run(run("doc", "body", backend=adapter, registry=reg_v1))

    monkeypatch.setattr("prompiler.spec.hash.COMPILER_PROTOCOL_VERSION", "999")
    reg_v2 = Registry()
    reg_v2.register("doc", compile_spec(spec))
    second = asyncio.run(run("doc", "body", backend=adapter, registry=reg_v2))

    assert adapter.calls == 2
    assert first.title == "v1"  # type: ignore[attr-defined]
    assert second.title == "v2"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_disable_cache_kwarg_forces_recompute() -> None:
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "one"}, {"title": "two"}])

    first = asyncio.run(run("doc", "body", backend=adapter, registry=registry, disable_cache=True))
    second = asyncio.run(run("doc", "body", backend=adapter, registry=registry, disable_cache=True))

    assert adapter.calls == 2
    assert first.title == "one"  # type: ignore[attr-defined]
    assert second.title == "two"  # type: ignore[attr-defined]
    info = result_cache_info()
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0


@pytest.mark.unit
def test_disable_cache_env_var_forces_recompute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_DISABLE_CACHE", "1")
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "one"}, {"title": "two"}])

    asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert result_cache_info().currsize == 0


@pytest.mark.unit
def test_disable_cache_env_var_non_one_value_keeps_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LL-005: the env gate compares string equality to ``"1"``, not truthiness."""
    monkeypatch.setenv("PROMPILER_DISABLE_CACHE", "0")
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "only"}])

    asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == 1


@pytest.mark.unit
def test_disable_cache_kwarg_false_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Precedence (LL-008): an explicit kwarg wins over the env var."""
    monkeypatch.setenv("PROMPILER_DISABLE_CACHE", "1")
    registry = _register("doc")
    adapter = _ScriptedAdapter([{"title": "only"}])

    asyncio.run(run("doc", "body", backend=adapter, registry=registry, disable_cache=False))
    asyncio.run(run("doc", "body", backend=adapter, registry=registry, disable_cache=False))

    assert adapter.calls == 1
