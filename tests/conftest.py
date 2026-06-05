"""Shared pytest fixtures and test helpers for the prompiler test suite.

This module is auto-loaded by pytest. Symbols defined here can be imported
from sibling test modules via ``from conftest import ...`` because pytest
inserts the conftest directory onto ``sys.path`` during collection.
"""

from __future__ import annotations

from typing import Any

from prompiler.backends.observability import (
    ObservabilityHook,
    PricingTable,
    emit_call_metrics,
)


class ScriptedAdapter:
    """Backend adapter that replays a queue of scripted responses.

    Each ``extract`` call pops the head of the queue:
    - a ``dict`` is returned as the adapter response;
    - an ``Exception`` is raised.

    Used by integration tests that exercise the orchestrator end-to-end
    against a deterministic adapter — no network, no API key.

    Pass ``observability`` (plus optional ``tokens``/``pricing``) to make a
    successful ``extract`` emit ``BackendCallMetrics`` — used by eval tests that
    exercise cost/token accounting. ``tokens`` is a per-call queue of
    ``(prompt_tokens, completion_tokens)`` consumed in lockstep with successful
    responses; it defaults to ``(0, 0)`` when exhausted.
    """

    def __init__(
        self,
        script: list[dict[str, Any] | Exception],
        *,
        observability: ObservabilityHook | None = None,
        pricing: PricingTable | None = None,
        tokens: list[tuple[int, int]] | None = None,
        backend_name: str = "scripted",
        model: str = "test-model",
    ) -> None:
        self._script: list[dict[str, Any] | Exception] = list(script)
        self.calls: int = 0
        self._observability = observability
        self._pricing = pricing if pricing is not None else PricingTable()
        self._tokens: list[tuple[int, int]] = list(tokens) if tokens is not None else []
        self._backend_name = backend_name
        self._model = model

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        head = self._script.pop(0)
        if isinstance(head, Exception):
            raise head
        if self._observability is not None:
            prompt_tokens, completion_tokens = self._tokens.pop(0) if self._tokens else (0, 0)
            await emit_call_metrics(
                hook=self._observability,
                backend=self._backend_name,
                model=self._model,
                latency_seconds=0.0,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                pricing=self._pricing,
            )
        return head

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)
