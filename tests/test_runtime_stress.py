"""Stress test for runtime orchestrator — PLAN.md L174.

Asserts that ``run_batch`` over 100 concurrent items does not exceed an
explicit peak-memory budget, measured with :mod:`tracemalloc`.

The budget is deliberately conservative (50 MB peak above baseline). The
goal is not micro-benchmarking; it is a regression net for accidental
unbounded buffering inside ``run_batch`` (e.g., capturing full prompts
per coroutine, leaking adapter state, retaining ``BaseModel`` instances
indefinitely).

Mirror the harness in ``test_runtime_orchestrator.py``: pytest-asyncio is
not installed, so coroutines run under ``asyncio.run`` inside a sync test
function.
"""

from __future__ import annotations

import asyncio
import tracemalloc
from typing import Any, Final

import pytest
from pydantic import BaseModel

from prompiler.compiler import compile_spec
from prompiler.runtime.orchestrator import run_batch
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_BATCH_SIZE: Final[int] = 100
_CONCURRENCY: Final[int] = 8
_PEAK_BUDGET_BYTES: Final[int] = 50 * 1024 * 1024
_INPUT_TEXT_BYTES: Final[int] = 5 * 1024
_RESPONSE_TITLE_BYTES: Final[int] = 5 * 1024


def _register() -> Registry:
    registry = Registry()
    bundle = compile_spec(
        EntitySpec.model_validate(
            {
                "spec_version": 1,
                "name": "doc",
                "task": "extract",
                "fields": [
                    {"name": "title", "type": "string", "required": True},
                ],
            }
        )
    )
    registry.register("doc", bundle)
    return registry


class _FastAdapter:
    """Returns a valid response immediately. No artificial delay.

    Response payload is intentionally non-trivial (~5 KB) so that
    accidental O(N) or O(N^2) buffering inside ``run_batch`` shows up
    against the 50 MB budget rather than getting lost in the noise.
    """

    def __init__(self) -> None:
        self.calls: int = 0
        self._payload: str = "x" * _RESPONSE_TITLE_BYTES

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        return {"title": self._payload}

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


@pytest.mark.unit
def test_run_batch_100_items_under_memory_budget() -> None:
    registry = _register()
    adapter = _FastAdapter()
    filler = "x" * _INPUT_TEXT_BYTES
    texts = [f"doc {i} {filler}" for i in range(_BATCH_SIZE)]

    tracemalloc.start()
    try:
        results = asyncio.run(
            run_batch(
                "doc",
                texts,
                backend=adapter,
                registry=registry,
                concurrency=_CONCURRENCY,
            )
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(results) == _BATCH_SIZE
    assert adapter.calls == _BATCH_SIZE
    assert all(isinstance(r, BaseModel) for r in results)
    assert peak < _PEAK_BUDGET_BYTES, (
        f"peak memory {peak} bytes exceeds budget {_PEAK_BUDGET_BYTES} bytes"
    )
