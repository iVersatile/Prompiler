"""Contract tests for the orchestrator streaming entrypoint — PLAN.md §2.4 G4.

``run_stream`` is the streaming sibling of ``run``: it yields each non-terminal
``StreamEvent`` (incremental deltas) as it arrives, then a single validated
``BaseModel`` as the final item. The adapter's terminal ``StreamEvent`` is
consumed internally — its assembled payload runs through the *same* Pydantic
``model_validate`` + single corrective re-extract as ``run``, and the validated
instance is yielded in its place (the terminal event itself is never re-yielded).

G4 exit criterion (verbatim): "a streaming run whose terminal payload validates
passes through; one whose terminal payload fails validation retries once then
raises ``ExtractionFailed``, matching non-streaming semantics exactly." The
parity test pins that the validated model from ``run_stream`` equals the one
from buffered ``run`` for an identical terminal payload.

pytest-asyncio is not installed; async generators are drained via ``asyncio.run``
inside sync test functions so the file runs under the default configuration.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from prompiler.backends.base import ExtractResult, ModalContent, StreamEvent
from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.errors import AdapterError
from prompiler.runtime.orchestrator import result_cache_info, run, run_stream
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_Attempt = tuple[list[str], dict[str, Any]]


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


class _ScriptedStreamingAdapter:
    """Streaming adapter replaying scripted ``(deltas, payload)`` attempts.

    Each ``stream_extract`` call pops the head of the script: a tuple
    ``(deltas, payload)`` streams one non-terminal ``StreamEvent`` per delta
    then a terminal ``StreamEvent`` carrying ``ExtractResult(data=payload)``; an
    ``Exception`` head is raised once iteration begins. ``extract`` shares the
    same queue so the adapter also satisfies the buffered ``BackendAdapter``
    contract (used by the parity test's ``run`` call). Prompts are captured in
    ``prompts``; ``calls`` counts attempts across both paths.
    """

    def __init__(self, script: list[_Attempt | Exception]) -> None:
        self._script: list[_Attempt | Exception] = list(script)
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
        _deltas, payload = head
        return ExtractResult(data=payload, system_fingerprint=None, deterministic=True)

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
        self.prompts.append(prompt)
        head = self._script.pop(0)
        if isinstance(head, Exception):
            raise head
        deltas, payload = head
        for delta in deltas:
            yield StreamEvent(delta=delta)
        yield StreamEvent(
            delta="",
            result=ExtractResult(data=payload, system_fingerprint=None, deterministic=True),
        )

    def supports(self, feature: str) -> bool:
        return feature in ("seed", "streaming")

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _drain(agen: AsyncIterator[Any]) -> list[Any]:
    async def _collect() -> list[Any]:
        return [item async for item in agen]

    return asyncio.run(_collect())


@pytest.mark.unit
def test_run_stream_yields_deltas_then_validated_model() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter([(['{"title":', ' "ok"}'], {"title": "ok"})])

    items = _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    events = [i for i in items if isinstance(i, StreamEvent)]
    assert [e.delta for e in events] == ['{"title":', ' "ok"}']
    assert isinstance(items[-1], BaseModel)
    assert items[-1].title == "ok"  # type: ignore[attr-defined]
    assert adapter.calls == 1


@pytest.mark.unit
def test_run_stream_terminal_event_is_not_re_yielded() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter([(["a", "b"], {"title": "ok"})])

    items = _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    assert not any(isinstance(i, StreamEvent) and i.is_terminal for i in items)
    models = [i for i in items if isinstance(i, BaseModel)]
    assert len(models) == 1
    assert items[-1] is models[0]


@pytest.mark.unit
def test_run_stream_retries_once_then_yields_validated_model() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter(
        [
            (["{", "bad"], {"wrong_key": "x"}),
            (["{", "good"], {"title": "ok"}),
        ]
    )

    items = _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    deltas = [i.delta for i in items if isinstance(i, StreamEvent)]
    assert deltas == ["{", "bad", "{", "good"]
    assert adapter.calls == 2
    assert isinstance(items[-1], BaseModel)
    assert items[-1].title == "ok"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_run_stream_second_attempt_prepends_corrective_feedback() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter(
        [
            (["x"], {"wrong_key": "x"}),
            (["y"], {"title": "ok"}),
        ]
    )

    _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert "Previous attempt failed validation" in adapter.prompts[1]


@pytest.mark.unit
def test_run_stream_raises_extraction_failed_after_two_validation_failures() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter(
        [
            (["x"], {"wrong_key": "x"}),
            (["y"], {"wrong_key": "y"}),
        ]
    )

    with pytest.raises(ExtractionFailed) as exc_info:
        _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert isinstance(exc_info.value.__cause__, ValidationError)


@pytest.mark.unit
def test_run_stream_propagates_adapter_error_without_retry() -> None:
    registry = _register("doc")
    boom = AdapterError("vendor down")
    adapter = _ScriptedStreamingAdapter([boom])

    with pytest.raises(AdapterError) as exc_info:
        _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    assert exc_info.value is boom
    assert adapter.calls == 1


@pytest.mark.unit
def test_run_stream_enforces_doc_size_guardrail_before_adapter_call() -> None:
    registry = _register("doc", max_input_tokens=4)
    adapter = _ScriptedStreamingAdapter([(["never"], {"title": "reached"})])

    with pytest.raises(ExtractionFailed):
        _drain(run_stream("doc", "x" * 1000, backend=adapter, registry=registry))

    assert adapter.calls == 0


@pytest.mark.unit
def test_run_stream_raises_keyerror_for_unknown_name() -> None:
    registry = Registry()
    adapter = _ScriptedStreamingAdapter([(["x"], {"title": "x"})])

    with pytest.raises(KeyError):
        _drain(run_stream("missing", "text", backend=adapter, registry=registry))

    assert adapter.calls == 0


@pytest.mark.unit
def test_run_stream_terminal_model_field_equals_buffered_run() -> None:
    registry = _register("doc")
    buffered_adapter = _ScriptedStreamingAdapter([([], {"title": "parity"})])
    streamed_adapter = _ScriptedStreamingAdapter([(["{", "x"], {"title": "parity"})])

    buffered = asyncio.run(run("doc", "body", backend=buffered_adapter, registry=registry))
    # ``disable_cache`` keeps this a genuine streaming run: the buffered ``run``
    # above populates the shared cache key (same spec/backend-class/text/prompt),
    # so without opting out ``run_stream`` would hit the cache and never stream.
    streamed = _drain(
        run_stream("doc", "body", backend=streamed_adapter, registry=registry, disable_cache=True)
    )[-1]

    assert isinstance(buffered, BaseModel)
    assert isinstance(streamed, BaseModel)
    assert buffered.title == streamed.title == "parity"  # type: ignore[attr-defined]
    assert streamed_adapter.calls == 1


# --- Track H1: result-cache interaction (PLAN.md §2.4) -----------------------
#
# H1 makes ``run_stream`` cache-aware with the *same* ``_RESULT_CACHE`` key as
# ``run`` (spec_hash, backend-class, model, input_hash, prompt_hash). The
# terminal validated model is memoized only after the stream fully drains; a
# cache hit replays as an already-complete (non-streaming) result — a single
# ``BaseModel`` yielded with no deltas and no adapter call.


@pytest.mark.unit
def test_run_stream_populates_cache_only_on_completion() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter([(["a", "b"], {"title": "ok"})])

    async def _scenario() -> None:
        agen = run_stream("doc", "body", backend=adapter, registry=registry)
        # Mid-stream: each non-terminal delta leaves the cache empty.
        first = await agen.__anext__()
        assert isinstance(first, StreamEvent)
        assert result_cache_info().currsize == 0
        second = await agen.__anext__()
        assert isinstance(second, StreamEvent)
        assert result_cache_info().currsize == 0
        # Drain completes: the validated model is yielded and the cache populated.
        final = await agen.__anext__()
        assert isinstance(final, BaseModel)
        assert result_cache_info().currsize == 1
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

    asyncio.run(_scenario())
    assert result_cache_info().misses == 1
    assert result_cache_info().hits == 0


@pytest.mark.unit
def test_run_stream_cache_hit_returns_full_model_without_restreaming() -> None:
    registry = _register("doc")
    first_adapter = _ScriptedStreamingAdapter([(["{", "x"], {"title": "ok"})])
    # Empty script: any adapter call pops from ``[]`` and raises IndexError.
    second_adapter = _ScriptedStreamingAdapter([])

    _drain(run_stream("doc", "body", backend=first_adapter, registry=registry))
    items = _drain(run_stream("doc", "body", backend=second_adapter, registry=registry))

    assert second_adapter.calls == 0
    assert not any(isinstance(i, StreamEvent) for i in items)
    assert len(items) == 1
    assert isinstance(items[0], BaseModel)
    assert items[0].title == "ok"  # type: ignore[attr-defined]
    assert result_cache_info().hits == 1


@pytest.mark.unit
def test_run_stream_aborted_stream_leaves_no_cache_entry() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter([(["a", "b", "c"], {"title": "ok"})])

    async def _scenario() -> None:
        agen = run_stream("doc", "body", backend=adapter, registry=registry)
        first = await agen.__anext__()
        assert isinstance(first, StreamEvent)
        await agen.aclose()

    asyncio.run(_scenario())
    assert result_cache_info().currsize == 0


@pytest.mark.unit
def test_run_stream_validation_failure_leaves_no_cache_entry() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter(
        [
            (["x"], {"wrong_key": "x"}),
            (["y"], {"wrong_key": "y"}),
        ]
    )

    with pytest.raises(ExtractionFailed):
        _drain(run_stream("doc", "body", backend=adapter, registry=registry))

    assert result_cache_info().currsize == 0
    assert result_cache_info().misses == 1


@pytest.mark.unit
def test_run_stream_disable_cache_skips_lookup_and_populate() -> None:
    registry = _register("doc")
    adapter = _ScriptedStreamingAdapter([(["x"], {"title": "ok"})])

    items = _drain(
        run_stream("doc", "body", backend=adapter, registry=registry, disable_cache=True)
    )

    assert isinstance(items[-1], BaseModel)
    assert result_cache_info().currsize == 0
    assert result_cache_info().misses == 0
    assert result_cache_info().hits == 0


@pytest.mark.unit
def test_run_stream_hits_cache_populated_by_buffered_run() -> None:
    registry = _register("doc")
    buffered_adapter = _ScriptedStreamingAdapter([([], {"title": "shared"})])
    # Empty script: proves the streaming path is never entered on a cache hit.
    streamed_adapter = _ScriptedStreamingAdapter([])

    asyncio.run(run("doc", "body", backend=buffered_adapter, registry=registry))
    items = _drain(run_stream("doc", "body", backend=streamed_adapter, registry=registry))

    assert streamed_adapter.calls == 0
    assert len(items) == 1
    assert isinstance(items[0], BaseModel)
    assert items[0].title == "shared"  # type: ignore[attr-defined]
    assert result_cache_info().hits == 1
