"""Integration test: multi-field / partial validation failures + retry bounce.

Companion to ``test_integration_retry_happy_path.py``. That file scripts a
single missing required field on turn 1. This file covers the gaps the others
skip:

- a turn-1 payload that fails on *multiple* required fields at once,
- a *partially* valid turn-1 payload (some fields good, others missing) that
  recovers on turn 2,
- a multi-field retry-then-succeed bounce where the corrective second prompt
  must name *every* offending field.

The orchestrator's one-shot retry budget is exercised end-to-end: turn-1
``ValidationError`` → turn-2 valid payload → typed instance returned, with the
second prompt carrying ``_CORRECTIVE_FEEDBACK_HEADER`` plus the first-turn
validation error (per ``orchestrator.py:56-69``).

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

import pytest
from pydantic import BaseModel

from prompiler.compiler import compile_spec
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_CORRECTIVE_MARKER: Final[str] = "Previous attempt failed validation."

# Three required string fields so several can fail simultaneously.
_INCIDENT_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "incident",
    "task": "extract",
    "fields": [
        {"name": "incident_id", "type": "string", "required": True},
        {"name": "severity", "type": "string", "required": True},
        {"name": "owner", "type": "string", "required": True},
    ],
}

_VALID_PAYLOAD: Final[dict[str, Any]] = {
    "incident_id": "INC-9001",
    "severity": "sev1",
    "owner": "oncall",
}

# Both severity and owner missing — two required fields fail at once.
_INVALID_MISSING_TWO: Final[dict[str, Any]] = {
    "incident_id": "INC-9001",
}

# Partially valid: incident_id + severity present, owner missing.
_PARTIAL_MISSING_OWNER: Final[dict[str, Any]] = {
    "incident_id": "INC-9001",
    "severity": "sev1",
}


def _register() -> Registry:
    registry = Registry()
    registry.register(
        "incident",
        compile_spec(EntitySpec.model_validate(_INCIDENT_SPEC)),
    )
    return registry


class _RecordingScriptedAdapter:
    """Scripted adapter that records the prompts it sees per call."""

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
    ) -> dict[str, Any]:
        self.calls += 1
        self.prompts.append(prompt)
        return self._script.pop(0)

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


@pytest.mark.integration
def test_retry_succeeds_after_multiple_fields_missing() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_INVALID_MISSING_TWO, _VALID_PAYLOAD])

    result = asyncio.run(run("incident", "incident body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert isinstance(result, BaseModel)
    assert result.incident_id == "INC-9001"
    assert result.severity == "sev1"
    assert result.owner == "oncall"


@pytest.mark.integration
def test_retry_prompt_names_every_missing_field() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_INVALID_MISSING_TWO, _VALID_PAYLOAD])

    asyncio.run(run("incident", "incident body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    first_prompt, second_prompt = adapter.prompts

    assert _CORRECTIVE_MARKER not in first_prompt
    assert _CORRECTIVE_MARKER in second_prompt
    # Both offending fields must be surfaced in the corrective prompt.
    assert "severity" in second_prompt
    assert "owner" in second_prompt
    assert first_prompt in second_prompt


@pytest.mark.integration
def test_retry_succeeds_after_partial_payload() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_PARTIAL_MISSING_OWNER, _VALID_PAYLOAD])

    result = asyncio.run(run("incident", "incident body", backend=adapter, registry=registry))

    assert adapter.calls == 2
    assert isinstance(result, BaseModel)
    assert result.owner == "oncall"


@pytest.mark.integration
def test_partial_payload_corrective_prompt_names_only_missing_field() -> None:
    registry = _register()
    adapter = _RecordingScriptedAdapter([_PARTIAL_MISSING_OWNER, _VALID_PAYLOAD])

    asyncio.run(run("incident", "incident body", backend=adapter, registry=registry))

    _, second_prompt = adapter.prompts

    assert _CORRECTIVE_MARKER in second_prompt
    assert "owner" in second_prompt
