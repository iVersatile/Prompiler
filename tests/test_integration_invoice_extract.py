"""Integration test: PRD §5.1 invoice use case — end-to-end extract pipeline.

Exercises the richest spec shape in the PRD:
- nested array of objects (line_items),
- ``decimal`` and ``date`` scalar types,
- ``enum`` (currency),
- regex ``pattern`` on a string field (invoice_number),
- ``cross_field_constraints`` with ``severity: error`` so violations surface as
  ``ValidationError`` rather than logged warnings.

The constraint severity deliberately diverges from PRD §5.1 (which shows
``warn``). The point of this test is to prove that constraint wiring reaches
Pydantic validation when configured as ``error`` — the more interesting failure
path. A separate unit test in ``test_compiler_constraints.py`` already covers
the ``warn`` path.

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Final

import pytest
from pydantic import BaseModel, ValidationError

from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_RETRY_BUDGET: Final[int] = 2

_INVOICE_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "invoice",
    "task": "extract",
    "cite": True,
    "fields": [
        {"name": "vendor_name", "type": "string", "required": True},
        {
            "name": "invoice_number",
            "type": "string",
            "required": True,
            "pattern": r"^[A-Z0-9-]{3,32}$",
        },
        {"name": "issue_date", "type": "date", "required": True},
        {"name": "total_amount", "type": "decimal", "required": True},
        {
            "name": "currency",
            "type": "enum",
            "required": True,
            "values": ["USD", "EUR", "GBP", "JPY", "CHF"],
        },
        {
            "name": "line_items",
            "type": "array",
            "required": True,
            "item": {
                "type": "object",
                "fields": [
                    {"name": "description", "type": "string", "required": True},
                    {"name": "quantity", "type": "integer", "required": True},
                    {"name": "unit_price", "type": "decimal", "required": True},
                    {"name": "line_total", "type": "decimal", "required": True},
                ],
            },
        },
    ],
    "cross_field_constraints": [
        {"expr": "sum(line_items.line_total) == total_amount", "severity": "error"},
    ],
}

_VALID_INVOICE_PAYLOAD: Final[dict[str, Any]] = {
    "vendor_name": "Acme Office Supplies, Inc.",
    "invoice_number": "ACME-2026-000142",
    "issue_date": "2026-05-17",
    "total_amount": "157.50",
    "currency": "USD",
    "line_items": [
        {
            "description": "Ergonomic mesh chair",
            "quantity": 1,
            "unit_price": "129.00",
            "line_total": "129.00",
        },
        {
            "description": "USB-C dock adapter",
            "quantity": 2,
            "unit_price": "14.25",
            "line_total": "28.50",
        },
    ],
}


class _ScriptedAdapter:
    def __init__(self, script: list[dict[str, Any] | Exception]) -> None:
        self._script: list[dict[str, Any] | Exception] = list(script)
        self.calls: int = 0

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
        return head

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register() -> Registry:
    registry = Registry()
    registry.register("invoice", compile_spec(EntitySpec.model_validate(_INVOICE_SPEC)))
    return registry


@pytest.mark.integration
def test_invoice_extract_happy_path_returns_typed_instance() -> None:
    registry = _register()
    adapter = _ScriptedAdapter([_VALID_INVOICE_PAYLOAD])

    result = asyncio.run(run("invoice", "raw invoice text", backend=adapter, registry=registry))

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.vendor_name == "Acme Office Supplies, Inc."
    assert result.invoice_number == "ACME-2026-000142"
    assert result.currency == "USD"
    assert result.total_amount == Decimal("157.50")
    assert len(result.line_items) == 2
    assert sum(item.line_total for item in result.line_items) == result.total_amount


@pytest.mark.integration
def test_invoice_extract_rejects_pattern_violation_on_invoice_number() -> None:
    registry = _register()
    bad = dict(_VALID_INVOICE_PAYLOAD, invoice_number="lower-case-no-good")
    adapter = _ScriptedAdapter([bad] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("invoice", "raw invoice text", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("invoice_number" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())


@pytest.mark.integration
def test_invoice_extract_rejects_cross_field_sum_mismatch() -> None:
    registry = _register()
    mismatched = dict(_VALID_INVOICE_PAYLOAD, total_amount="999.99")
    adapter = _ScriptedAdapter([mismatched] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("invoice", "raw invoice text", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
