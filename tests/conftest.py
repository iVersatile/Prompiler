"""Shared pytest fixtures and test helpers for the prompiler test suite.

This module is auto-loaded by pytest. Symbols defined here can be imported
from sibling test modules via ``from conftest import ...`` because pytest
inserts the conftest directory onto ``sys.path`` during collection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

import prompiler.runtime.registry as registry_module
from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.backends.observability import (
    ObservabilityHook,
    PricingTable,
    emit_call_metrics,
)
from prompiler.runtime.registry import Registry


@pytest.fixture(autouse=True)
def _isolated_default_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a fresh module-level default registry.

    The default registry is a process-global singleton; tests that register
    into it without an explicit ``registry=`` kwarg (e.g. the e2e client tests)
    would otherwise leak names across tests and trigger spurious duplicate-name
    ``ValueError``s depending on collection order.
    """
    monkeypatch.setattr(registry_module, "_DEFAULT_REGISTRY", Registry())


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
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> ExtractResult:
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
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)
