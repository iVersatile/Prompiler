"""Integration test: PRD §4 use case 4 — contract obligation extraction.

Exercises a shape the other integration tests don't:

- a thin envelope: a single top-level ``array`` of ``object`` (no flat scalar
  header fields beside it),
- typed ``date`` leaves (validated/coerced by pydantic),
- optional ``decimal`` leaves (``Decimal`` round-trip via string input).

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
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

_CONTRACT_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "contract_obligations",
    "task": "extract",
    "fields": [
        {
            "name": "obligations",
            "type": "array",
            "required": True,
            "item": {
                "type": "object",
                "fields": [
                    {"name": "party", "type": "string", "required": True},
                    {"name": "action", "type": "string", "required": True},
                    {"name": "deadline", "type": "date", "required": True},
                    {
                        "name": "penalty_amount",
                        "type": "decimal",
                        "required": False,
                    },
                ],
            },
        },
    ],
}

_VALID_PAYLOAD: Final[dict[str, Any]] = {
    "obligations": [
        {
            "party": "Acme Corp",
            "action": "Deliver 500 widgets to warehouse B",
            "deadline": "2026-09-30",
            "penalty_amount": "1000.00",
        },
        {
            "party": "Beta LLC",
            "action": "Provide quarterly compliance report",
            "deadline": "2026-10-07",
            "penalty_amount": None,
        },
    ],
}


def _register() -> Registry:
    registry = Registry()
    registry.register(
        "contract_obligations",
        compile_spec(EntitySpec.model_validate(_CONTRACT_SPEC)),
    )
    return registry


@pytest.mark.integration
def test_contract_obligations_happy_path_typed_dates_and_decimals() -> None:
    registry = _register()
    adapter = ScriptedAdapter([_VALID_PAYLOAD])

    result = asyncio.run(
        run("contract_obligations", "contract body", backend=adapter, registry=registry)
    )

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert len(result.obligations) == 2

    first = result.obligations[0]
    assert first.party == "Acme Corp"
    assert first.deadline == date(2026, 9, 30)
    assert first.penalty_amount == Decimal("1000.00")

    second = result.obligations[1]
    assert second.party == "Beta LLC"
    assert second.deadline == date(2026, 10, 7)
    assert second.penalty_amount is None


@pytest.mark.integration
def test_contract_obligations_accepts_empty_array() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"obligations": []}])

    result = asyncio.run(
        run("contract_obligations", "no obligations", backend=adapter, registry=registry)
    )

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.obligations == []


@pytest.mark.integration
def test_contract_obligations_rejects_malformed_deadline() -> None:
    registry = _register()
    bad = {
        "obligations": [
            {
                "party": "Acme Corp",
                "action": "Deliver widgets",
                "deadline": "not-a-date",
            },
        ],
    }
    adapter = ScriptedAdapter([bad] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("contract_obligations", "bad deadline", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("deadline" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())
