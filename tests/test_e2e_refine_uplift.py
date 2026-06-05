"""E2E forced-regression test: the refine loop restores F1 (P5, L217/L224).

PLAN.md L217 (acceptance): "Forced-regression fixture: degrade prompt by hand ->
``refine`` proposes a patch that restores >= original F1."
PLAN.md L224 (DoD): "E2E test using a canned fixture proves measurable F1 uplift
on at least one of the demo specs."

The loop is exercised end-to-end with zero network: a degraded prompt yields a
degraded extraction (wrong values on the scored fields -> F1 0.0); a scripted
tutor returns a unified diff; ``apply_patch`` rewrites the prompt; the gold
extraction then scores F1 1.0. ``compute_delta`` confirms the uplift.

The backend is fully scripted and prompt-ignoring: it returns whichever payload
the test hands it, so the F1 swing is driven entirely by which payload each
``run_eval`` is wired to — mirroring a real before/after refine cycle while
staying deterministic. The invoice spec is the richest demo spec (PRD §5.1);
only its plain-string fields (vendor_name, invoice_number) are scored to avoid
enum/date/decimal ``str()`` ambiguity in the differ.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from prompiler.compiler import compile_spec
from prompiler.eval.fixtures import FixtureCase
from prompiler.eval.runner import run_eval
from prompiler.refine import apply_patch, compute_delta, propose_patch_sync
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

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

_GOLD_INVOICE_PAYLOAD: Final[dict[str, Any]] = {
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

# Degraded extraction: spec-valid (invoice_number still matches the pattern, the
# line-item sum still equals total_amount) but both scored string fields are
# wrong, so exact scoring lands F1 0.0 rather than a validation failure.
_DEGRADED_INVOICE_PAYLOAD: Final[dict[str, Any]] = {
    **_GOLD_INVOICE_PAYLOAD,
    "vendor_name": "Wrong Vendor LLC",
    "invoice_number": "WRONG-001",
}

# Only plain-string fields are scored — see module docstring.
_SCORED_EXPECTED: Final[dict[str, str]] = {
    "vendor_name": "Acme Office Supplies, Inc.",
    "invoice_number": "ACME-2026-000142",
}

_DEGRADED_PROMPT: Final[str] = "Extract the invoice fields.\nReturn the vendor name as written.\n"

# Context line 1 is preserved; line 2 is rewritten. Loose headers are fine —
# differ.apply_patch matches by context subsequence, not header line counts.
_TUTOR_DIFF: Final[str] = (
    "--- prompt.txt\n"
    "+++ prompt.txt\n"
    "@@ -1,2 +1,2 @@\n"
    " Extract the invoice fields.\n"
    "-Return the vendor name as written.\n"
    "+Return the exact vendor name and invoice number verbatim from the document.\n"
)


class _ScriptedAdapter:
    """Prompt-ignoring backend: returns scripted payloads in order.

    Local to this test (mirrors the sibling integration test) so the E2E case
    owns its own fault-free, deterministic double rather than importing one.
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script: list[dict[str, Any]] = list(script)
        self.calls: int = 0

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        assert self._script, (
            f"_ScriptedAdapter exhausted on call {self.calls}; "
            "test scripted fewer responses than the runner requested"
        )
        return self._script.pop(0)

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register() -> Registry:
    registry = Registry()
    registry.register("invoice", compile_spec(EntitySpec.model_validate(_INVOICE_SPEC)))
    return registry


def _build_report(spec_name: str, f1: float) -> dict[str, Any]:
    """Minimal eval-report dict shaped for the tutor prompt builder."""
    return {
        "spec": spec_name,
        "backend": "scripted",
        "model": "test-model",
        "aggregate": {"precision": f1, "recall": f1, "f1": f1},
        "per_field": {field: {"p": f1, "r": f1, "f1": f1} for field in _SCORED_EXPECTED},
    }


@pytest.mark.integration
def test_refine_restores_f1_on_invoice_spec() -> None:
    registry = _register()
    case = FixtureCase(name="acme_invoice", input="raw invoice text", expected=_SCORED_EXPECTED)

    # 1. Degraded prompt -> degraded extraction -> F1 floor.
    before = run_eval(
        "invoice",
        [case],
        backend=_ScriptedAdapter([_DEGRADED_INVOICE_PAYLOAD]),
        registry=registry,
    )
    assert before.metrics.f1 == 0.0

    # 2. Tutor proposes a patch from the (degraded) report; apply it.
    diff = propose_patch_sync(
        report=_build_report("invoice", before.metrics.f1),
        current_prompt=_DEGRADED_PROMPT,
        backend=_ScriptedAdapter([{"decline": False, "diff": _TUTOR_DIFF}]),
    )
    patched_prompt = apply_patch(_DEGRADED_PROMPT, diff)
    assert patched_prompt != _DEGRADED_PROMPT

    # 3. Refined prompt -> gold extraction -> F1 restored.
    after = run_eval(
        "invoice",
        [case],
        backend=_ScriptedAdapter([_GOLD_INVOICE_PAYLOAD]),
        registry=registry,
    )
    assert after.metrics.f1 == 1.0

    # 4. Measurable, non-negative uplift (L217: restores >= original F1).
    delta = compute_delta(before.metrics, after.metrics)
    assert delta.improved is True
    assert after.metrics.f1 >= before.metrics.f1
