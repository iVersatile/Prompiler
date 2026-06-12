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

import dataclasses
from collections.abc import Sequence
from typing import Any, Final

import pytest

from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.compiler import compile_spec
from prompiler.eval.fixtures import FixtureCase
from prompiler.eval.runner import run_eval
from prompiler.refine import apply_patch, compute_delta, propose_patch_sync
from prompiler.runtime.errors import AdapterError
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_INVOICE_SPEC: Final[dict[str, Any]] = {
    "spec_version": 2,
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


# The marker the refined instruction injects. The prompt-sensitive adapter keys
# on this substring so its output is a *function of the prompt content*, not of
# call order — that is what upgrades the test from choreography to causality.
_REFINED_MARKER: Final[str] = "exact vendor name and invoice number verbatim"


class _PromptSensitiveAdapter:
    """Causal backend: output is a function of the prompt it receives.

    Returns the gold payload iff the assembled prompt carries the refined
    instruction (``_REFINED_MARKER``); otherwise the degraded payload. A single
    instance is reused across both ``run_eval`` calls, so the F1 swing can only
    come from the prompt changing — not from a scripted call sequence (which is
    what the prompt-IGNORING double in the sibling integration test does).
    """

    def __init__(self) -> None:
        self.seen_prompts: list[str] = []

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
        self.seen_prompts.append(prompt)
        refined = _REFINED_MARKER in prompt
        payload = dict(_GOLD_INVOICE_PAYLOAD) if refined else dict(_DEGRADED_INVOICE_PAYLOAD)
        return ExtractResult(data=payload, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


class _TutorAdapter:
    """Tutor backend: returns one fixed proposal payload regardless of prompt.

    The tutor's job here is fully decided by the test (it must yield ``diff``);
    its prompt-sensitivity is irrelevant, so a fixed payload keeps the E2E focus
    on the extraction adapter's causal behaviour.
    """

    def __init__(self, diff: str) -> None:
        self._payload: dict[str, Any] = {"decline": False, "diff": diff}

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
        return ExtractResult(data=dict(self._payload), system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register(prompt: str) -> Registry:
    """Registry whose invoice bundle carries ``prompt`` as its base prompt.

    ``run_eval`` has no prompt parameter — the base prompt fed to the adapter is
    ``bundle.prompt`` (orchestrator assembles ``bundle.prompt`` + input block).
    ArtefactBundle is a frozen dataclass, so ``dataclasses.replace`` lets the
    test pin the exact prompt the adapter will see, routing the patched text in.
    """
    registry = Registry()
    compiled = compile_spec(EntitySpec.model_validate(_INVOICE_SPEC))
    registry.register("invoice", dataclasses.replace(compiled, prompt=prompt))
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


@pytest.mark.e2e
def test_refine_restores_f1_on_invoice_spec() -> None:
    case = FixtureCase(name="acme_invoice", input="raw invoice text", expected=_SCORED_EXPECTED)

    # ONE adapter instance spans both runs: output is a function of the prompt it
    # sees, never of call order. The F1 swing is therefore *caused* by the prompt
    # changing between runs — causality, not the choreography a scripted double
    # would prove (LL-006: the prompt-ignoring double can only show plumbing).
    backend = _PromptSensitiveAdapter()

    # 1. Degraded prompt (no refined marker) -> adapter degrades -> F1 floor.
    before = run_eval("invoice", [case], backend=backend, registry=_register(_DEGRADED_PROMPT))
    assert before.metrics.f1 == 0.0

    # 2. Tutor proposes a patch from the (degraded) report; apply it.
    diff = propose_patch_sync(
        report=_build_report("invoice", before.metrics.f1),
        current_prompt=_DEGRADED_PROMPT,
        backend=_TutorAdapter(_TUTOR_DIFF),
    )
    assert diff == _TUTOR_DIFF
    patched_prompt = apply_patch(_DEGRADED_PROMPT, diff)
    assert patched_prompt != _DEGRADED_PROMPT
    assert _REFINED_MARKER in patched_prompt
    assert "Return the vendor name as written." not in patched_prompt

    # 3. Patched prompt is routed into run_eval via the registry bundle. The SAME
    # adapter now sees the refined marker -> emits gold -> F1 restored. Nothing
    # but the prompt differs from run 1.
    after = run_eval("invoice", [case], backend=backend, registry=_register(patched_prompt))
    assert after.metrics.f1 == 1.0

    # 4. The adapter saw the degraded prompt first, the patched prompt second —
    # proving the runner actually fed bundle.prompt through to extract().
    assert len(backend.seen_prompts) == 2
    assert _REFINED_MARKER not in backend.seen_prompts[0]
    assert _REFINED_MARKER in backend.seen_prompts[1]

    # 5. Measurable, non-negative uplift (L217: restores >= original F1).
    delta = compute_delta(before.metrics, after.metrics)
    assert delta.improved is True
    assert after.metrics.f1 >= before.metrics.f1


class _DecliningTutorAdapter:
    """Tutor backend that refuses: returns ``decline=True`` with a reason.

    Drives the decline branch of ``propose_patch`` (tutor.py L101) so the e2e
    tier proves the *no-uplift* path — the tutor judging the prompt unimprovable
    — not just the happy path. ``propose_patch_sync`` must surface this as an
    ``AdapterError`` and leave the prompt untouched, so F1 stays at the floor.
    """

    def __init__(self, reason: str) -> None:
        self._payload: dict[str, Any] = {"decline": True, "reason": reason}

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
        return ExtractResult(data=dict(self._payload), system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


@pytest.mark.e2e
def test_refine_decline_leaves_prompt_and_f1_at_floor() -> None:
    """Tutor declines -> ``AdapterError`` -> prompt unchanged -> F1 stays at floor.

    The uplift test proves refine *can* recover F1; this proves the loop fails
    safe when the tutor refuses: no patch is applied, the degraded prompt is
    re-run verbatim, and the second eval reports the same floor F1 as the first.
    """
    case = FixtureCase(name="acme_invoice", input="raw invoice text", expected=_SCORED_EXPECTED)
    backend = _PromptSensitiveAdapter()

    before = run_eval("invoice", [case], backend=backend, registry=_register(_DEGRADED_PROMPT))
    assert before.metrics.f1 == 0.0

    with pytest.raises(AdapterError, match="tutor declined"):
        propose_patch_sync(
            report=_build_report("invoice", before.metrics.f1),
            current_prompt=_DEGRADED_PROMPT,
            backend=_DecliningTutorAdapter("prompt already optimal"),
        )

    # No patch -> the degraded prompt is re-run unchanged. F1 stays at the floor.
    after = run_eval("invoice", [case], backend=backend, registry=_register(_DEGRADED_PROMPT))
    assert after.metrics.f1 == 0.0

    delta = compute_delta(before.metrics, after.metrics)
    assert delta.improved is False
    assert all(_REFINED_MARKER not in seen for seen in backend.seen_prompts)


# --- Second extract spec: breadth beyond the invoice demo (gap G1) -----------
#
# A distinct extract spec with its own scored string fields, proving the uplift
# path is not invoice-specific. Same causal mechanism: the adapter emits gold
# iff the refined marker is present in the prompt.

_CONTACT_SPEC: Final[dict[str, Any]] = {
    "spec_version": 2,
    "name": "contact",
    "task": "extract",
    "fields": [
        {"name": "full_name", "type": "string", "required": True},
        {"name": "email", "type": "string", "required": True},
    ],
}

_CONTACT_GOLD: Final[dict[str, str]] = {
    "full_name": "Dana Whitfield",
    "email": "dana.whitfield@example.com",
}

_CONTACT_DEGRADED: Final[dict[str, str]] = {
    "full_name": "Unknown Person",
    "email": "n/a@example.com",
}

_CONTACT_DEGRADED_PROMPT: Final[str] = "Extract the contact.\nGuess the name if unclear.\n"

_CONTACT_REFINED_MARKER: Final[str] = "copy the full name and email verbatim"

_CONTACT_DIFF: Final[str] = (
    "--- prompt.txt\n"
    "+++ prompt.txt\n"
    "@@ -1,2 +1,2 @@\n"
    " Extract the contact.\n"
    "-Guess the name if unclear.\n"
    "+Copy the full name and email verbatim from the source text.\n"
)


class _ContactAdapter:
    """Causal contact-extraction backend keyed on ``_CONTACT_REFINED_MARKER``."""

    def __init__(self) -> None:
        self.seen_prompts: list[str] = []

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
        self.seen_prompts.append(prompt)
        refined = _CONTACT_REFINED_MARKER in prompt.lower()
        payload = dict(_CONTACT_GOLD) if refined else dict(_CONTACT_DEGRADED)
        return ExtractResult(data=payload, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register_contact(prompt: str) -> Registry:
    registry = Registry()
    compiled = compile_spec(EntitySpec.model_validate(_CONTACT_SPEC))
    registry.register("contact", dataclasses.replace(compiled, prompt=prompt))
    return registry


@pytest.mark.e2e
def test_refine_restores_f1_on_contact_spec() -> None:
    """Uplift on a second, non-invoice extract spec — proves breadth (gap G1)."""
    case = FixtureCase(name="dana", input="raw contact text", expected=_CONTACT_GOLD)
    backend = _ContactAdapter()

    before = run_eval(
        "contact", [case], backend=backend, registry=_register_contact(_CONTACT_DEGRADED_PROMPT)
    )
    assert before.metrics.f1 == 0.0

    diff = propose_patch_sync(
        report=_build_report("contact", before.metrics.f1),
        current_prompt=_CONTACT_DEGRADED_PROMPT,
        backend=_TutorAdapter(_CONTACT_DIFF),
    )
    patched_prompt = apply_patch(_CONTACT_DEGRADED_PROMPT, diff)
    assert _CONTACT_REFINED_MARKER in patched_prompt.lower()

    after = run_eval("contact", [case], backend=backend, registry=_register_contact(patched_prompt))
    assert after.metrics.f1 == 1.0

    delta = compute_delta(before.metrics, after.metrics)
    assert delta.improved is True
