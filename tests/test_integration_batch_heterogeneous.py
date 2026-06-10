"""Integration test: heterogeneous batch — caller-orchestrated gather.

Distinct angle from the other integration tests: ``run_batch`` is *single-name*
(see ``runtime/orchestrator.py``) — it fans the same compiled spec over many
input texts. Heterogeneous batches (mixing distinct entity specs in one
submission) are the *caller's* responsibility, expressed as
``asyncio.gather(run(name_a, ...), run(name_b, ...), ...)`` against a shared
adapter.

This file pins that idiom end-to-end:

- two specs with disjoint field shapes (note: ``title`` + ``body``;
  ticket: ``ticket_id`` + ``priority``),
- one scripted adapter that dispatches by the schema's property set
  (``frozenset(json_schema["properties"].keys())``), so the adapter can be
  shared across both spec types without leaking state between them,
- per-spec FIFO queues that accept either a payload dict or an ``Exception``
  to inject, so retry/failure scenarios for one spec do not consume the
  other spec's queue.

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
from prompiler.runtime.errors import ExtractionFailed
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

_TICKET_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "support_ticket",
    "task": "extract",
    "fields": [
        {"name": "ticket_id", "type": "string", "required": True},
        {"name": "priority", "type": "string", "required": True},
    ],
}

_NOTE_PROPS: Final[frozenset[str]] = frozenset({"title", "body"})
_TICKET_PROPS: Final[frozenset[str]] = frozenset({"ticket_id", "priority"})

_NOTE_PAYLOAD: Final[dict[str, Any]] = {
    "title": "Outage post-mortem",
    "body": "Root cause identified in deploy 8f2a1c.",
}

_TICKET_PAYLOAD: Final[dict[str, Any]] = {
    "ticket_id": "SUP-4821",
    "priority": "high",
}

_TICKET_INVALID_MISSING_PRIORITY: Final[dict[str, Any]] = {
    "ticket_id": "SUP-4821",
}


class _SchemaDispatchScriptedAdapter:
    """Scripted adapter that dispatches by the schema's property set.

    A single adapter instance serves multiple distinct specs in one batch.
    Per-spec FIFO queues isolate state: popping for spec A never advances
    spec B's cursor. Queues accept payload dicts *or* ``Exception`` instances
    (the latter are raised on pop) so retry/failure scenarios can be wired
    per-spec without cross-talk.
    """

    def __init__(self, queues: dict[frozenset[str], list[dict[str, Any] | Exception]]) -> None:
        self._queues: dict[frozenset[str], list[dict[str, Any] | Exception]] = {
            key: list(items) for key, items in queues.items()
        }
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
        key = frozenset(json_schema["properties"].keys())
        queue = self._queues[key]
        head = queue.pop(0)
        if isinstance(head, Exception):
            raise head
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register_both() -> Registry:
    registry = Registry()
    registry.register("short_note", compile_spec(EntitySpec.model_validate(_NOTE_SPEC)))
    registry.register(
        "support_ticket",
        compile_spec(EntitySpec.model_validate(_TICKET_SPEC)),
    )
    return registry


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()


@pytest.mark.integration
def test_heterogeneous_batch_returns_per_spec_typed_instances() -> None:
    registry = _register_both()
    adapter = _SchemaDispatchScriptedAdapter(
        {
            _NOTE_PROPS: [_NOTE_PAYLOAD],
            _TICKET_PROPS: [_TICKET_PAYLOAD],
        }
    )

    async def _gather() -> tuple[BaseModel, BaseModel]:
        note, ticket = await asyncio.gather(
            run("short_note", "note body", backend=adapter, registry=registry),
            run(
                "support_ticket",
                "ticket body",
                backend=adapter,
                registry=registry,
            ),
        )
        return note, ticket

    note_result, ticket_result = asyncio.run(_gather())

    assert isinstance(note_result, BaseModel)
    assert isinstance(ticket_result, BaseModel)
    assert type(note_result) is not type(ticket_result)
    assert _dump(note_result) == _NOTE_PAYLOAD
    assert _dump(ticket_result) == _TICKET_PAYLOAD
    assert adapter.calls == 2


@pytest.mark.integration
def test_heterogeneous_batch_preserves_submission_order() -> None:
    registry = _register_both()
    adapter = _SchemaDispatchScriptedAdapter(
        {
            _NOTE_PROPS: [_NOTE_PAYLOAD, _NOTE_PAYLOAD],
            _TICKET_PROPS: [_TICKET_PAYLOAD, _TICKET_PAYLOAD],
        }
    )

    async def _gather() -> list[BaseModel]:
        return list(
            await asyncio.gather(
                run(
                    "support_ticket",
                    "t1",
                    backend=adapter,
                    registry=registry,
                ),
                run("short_note", "n1", backend=adapter, registry=registry),
                run(
                    "support_ticket",
                    "t2",
                    backend=adapter,
                    registry=registry,
                ),
                run("short_note", "n2", backend=adapter, registry=registry),
            )
        )

    results = asyncio.run(_gather())

    dumps = [_dump(r) for r in results]
    assert dumps == [
        _TICKET_PAYLOAD,
        _NOTE_PAYLOAD,
        _TICKET_PAYLOAD,
        _NOTE_PAYLOAD,
    ]
    assert adapter.calls == 4


@pytest.mark.integration
def test_heterogeneous_batch_per_spec_failure_isolated_with_return_exceptions() -> None:
    registry = _register_both()
    adapter = _SchemaDispatchScriptedAdapter(
        {
            _NOTE_PROPS: [_NOTE_PAYLOAD],
            _TICKET_PROPS: [
                _TICKET_INVALID_MISSING_PRIORITY,
                _TICKET_INVALID_MISSING_PRIORITY,
            ],
        }
    )

    async def _gather() -> list[BaseModel | BaseException]:
        return list(
            await asyncio.gather(
                run("short_note", "n", backend=adapter, registry=registry),
                run(
                    "support_ticket",
                    "t",
                    backend=adapter,
                    registry=registry,
                ),
                return_exceptions=True,
            )
        )

    note_result, ticket_result = asyncio.run(_gather())

    assert isinstance(note_result, BaseModel)
    assert _dump(note_result) == _NOTE_PAYLOAD
    assert isinstance(ticket_result, ExtractionFailed)
    assert adapter.calls == 3
