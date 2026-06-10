"""Integration test: corrective-feedback retry round-trip.

Distinct angle from the other integration tests: those scripts return *either*
all-valid payloads (happy path) or all-invalid payloads (rejection path). This
file exercises the orchestrator's one-shot retry budget end-to-end:

- turn 1 returns a payload missing a required field → ``ValidationError``,
- turn 2 returns a valid payload → orchestrator returns the typed instance,
- the *second* prompt the adapter sees carries the
  ``_CORRECTIVE_FEEDBACK_HEADER`` plus the first-turn validation error
  (per ``orchestrator.py:56-69``).

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Final

import pytest
from pydantic import BaseModel

from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.compiler import compile_spec
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_CORRECTIVE_MARKER: Final[str] = "Previous attempt failed validation."

_SUPPORT_TICKET_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "support_ticket",
    "task": "extract",
    "fields": [
        {"name": "ticket_id", "type": "string", "required": True},
        {"name": "priority", "type": "string", "required": True},
    ],
}

_VALID_PAYLOAD: Final[dict[str, Any]] = {
    "ticket_id": "SUP-4821",
    "priority": "high",
}

_INVALID_PAYLOAD_MISSING_PRIORITY: Final[dict[str, Any]] = {
    "ticket_id": "SUP-4821",
}


class _RecordingScriptedAdapter:
    """Scripted adapter that also records the prompts it sees per call."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script: list[dict[str, Any]] = list(script)
        self.calls: int = 0
        self.prompts: list[str] = []

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
        return ExtractResult(data=self._script.pop(0), system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register() -> Registry:
    registry = Registry()
    registry.register(
        "support_ticket",
        compile_spec(EntitySpec.model_validate(_SUPPORT_TICKET_SPEC)),
    )
    return registry


@pytest.mark.integration
def test_first_attempt_succeeds_no_retry() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_VALID_PAYLOAD])

    result = asyncio.run(run("support_ticket", "ticket body", backend=adapter, registry=registry))

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.ticket_id == "SUP-4821"
    assert result.priority == "high"
    assert _CORRECTIVE_MARKER not in adapter.prompts[0]


@pytest.mark.integration
def test_retry_succeeds_after_one_validation_failure() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_INVALID_PAYLOAD_MISSING_PRIORITY, _VALID_PAYLOAD])

    result = asyncio.run(run("support_ticket", "ticket body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert isinstance(result, BaseModel)
    assert result.ticket_id == "SUP-4821"
    assert result.priority == "high"


@pytest.mark.integration
def test_retry_prompt_carries_corrective_header_and_validation_error() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_INVALID_PAYLOAD_MISSING_PRIORITY, _VALID_PAYLOAD])

    asyncio.run(run("support_ticket", "ticket body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    first_prompt, second_prompt = adapter.prompts

    assert _CORRECTIVE_MARKER not in first_prompt
    assert _CORRECTIVE_MARKER in second_prompt
    assert "priority" in second_prompt
    assert first_prompt in second_prompt
