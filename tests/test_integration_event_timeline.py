"""Integration test: PRD §4 use case 6 — incident report event timeline extract.

Exercises a shape the other integration tests don't:

- typed ``datetime`` leaves (ISO-8601 string → ``datetime`` via pydantic
  coercion) on the required key of each event,
- a single thin envelope (top-level ``events`` array of objects) with a
  *required* scalar enum inside each item via ``values:`` — distinct from
  contract_obligations which uses a free-form ``string``.

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from pydantic import BaseModel, ValidationError

from conftest import ScriptedAdapter
from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_RETRY_BUDGET: Final[int] = 2

_TIMELINE_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "incident_timeline",
    "task": "extract",
    "fields": [
        {
            "name": "events",
            "type": "array",
            "required": True,
            "item": {
                "type": "object",
                "fields": [
                    {"name": "timestamp", "type": "datetime", "required": True},
                    {"name": "actor", "type": "string", "required": True},
                    {
                        "name": "event_type",
                        "type": "enum",
                        "required": True,
                        "values": ["detection", "mitigation", "resolution"],
                    },
                    {"name": "summary", "type": "string", "required": False},
                ],
            },
        },
    ],
}

_VALID_PAYLOAD: Final[dict[str, Any]] = {
    "events": [
        {
            "timestamp": "2026-05-15T14:32:00Z",
            "actor": "on-call-1",
            "event_type": "detection",
            "summary": "Alert fired on p99 latency breach.",
        },
        {
            "timestamp": "2026-05-15T14:47:00Z",
            "actor": "sre-2",
            "event_type": "mitigation",
            "summary": None,
        },
        {
            "timestamp": "2026-05-15T15:10:00Z",
            "actor": "sre-2",
            "event_type": "resolution",
            "summary": "Rolled back deploy 8f2a1c.",
        },
    ],
}


def _register() -> Registry:
    registry = Registry()
    registry.register(
        "incident_timeline",
        compile_spec(EntitySpec.model_validate(_TIMELINE_SPEC)),
    )
    return registry


@pytest.mark.integration
def test_event_timeline_happy_path_typed_datetimes_and_enum() -> None:
    registry = _register()
    adapter = ScriptedAdapter([_VALID_PAYLOAD])

    result = asyncio.run(
        run("incident_timeline", "incident body", backend=adapter, registry=registry)
    )

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert len(result.events) == 3

    first = result.events[0]
    assert first.timestamp == datetime(2026, 5, 15, 14, 32, 0, tzinfo=UTC)
    assert first.actor == "on-call-1"
    assert first.event_type == "detection"
    assert first.summary == "Alert fired on p99 latency breach."

    second = result.events[1]
    assert second.event_type == "mitigation"
    assert second.summary is None

    third = result.events[2]
    assert third.event_type == "resolution"


@pytest.mark.integration
def test_event_timeline_accepts_empty_array() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"events": []}])

    result = asyncio.run(run("incident_timeline", "no events", backend=adapter, registry=registry))

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.events == []


@pytest.mark.integration
def test_event_timeline_rejects_malformed_timestamp() -> None:
    registry = _register()
    bad = {
        "events": [
            {
                "timestamp": "not-a-timestamp",
                "actor": "on-call-1",
                "event_type": "detection",
            },
        ],
    }
    adapter = ScriptedAdapter([bad] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("incident_timeline", "bad ts", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("timestamp" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())


@pytest.mark.integration
def test_event_timeline_rejects_out_of_vocab_event_type() -> None:
    registry = _register()
    bad = {
        "events": [
            {
                "timestamp": "2026-05-15T14:32:00Z",
                "actor": "on-call-1",
                "event_type": "escalation",
            },
        ],
    }
    adapter = ScriptedAdapter([bad] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("incident_timeline", "bad enum", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("event_type" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())
