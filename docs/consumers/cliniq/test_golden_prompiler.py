"""DRAFT — golden-test rewrite for the prompiler-backed extractors.

This is the mapping-parity layer (Layer 1 in README.md): it proves the seam
moves cleanly from cliniq's ``adapter.complete_json`` to prompiler's
``BackendAdapter.extract`` and that the list-shaped payload round-trips through
``run_sync`` → ``model_dump()[field]`` → cliniq model unchanged. It deliberately
does NOT exercise a real LLM or the compiled prompt text (Layer 2 — prompt/output
parity — is out of scope for this harness).

The mock seam: ``_FakeBackend`` returns a pre-baked ``ExtractResult`` instead of
calling a model, so the golden list is wrapped as ``{"medications": [...]}`` /
``{"appointments": [...]}`` to match each spec's top-level array field.

Not pytest-collected in this repo (``testpaths = ["tests"]`` excludes docs/).
Lives here as a reference draft; it runs once dropped into cliniq's test tree
alongside the rewritten extractors, with cliniq + prompiler both importable.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.runtime.registry import Registry, register_from_path

from extract_appointments_prompiler import extract_appointments
from extract_medications_prompiler import extract_medications

HERE = Path(__file__).parent

_MEDICATIONS_GOLDEN: list[dict[str, Any]] = [
    {
        "name": "Lansoprazole",
        "dose": "15 mg",
        "frequency": "once daily",
        "citation": "Lansoprazole 15 mg once daily prescribed for gastric protection.",
    },
    {
        "name": "Cetirizine",
        "dose": "10 mg",
        "frequency": "once daily",
        "citation": "Cetirizine 10 mg once daily for allergic rhinitis management.",
    },
]

_APPOINTMENTS_GOLDEN: list[dict[str, Any]] = [
    {
        "date": "2022-03-10",
        "reason": "Follow-up consultation and blood results review",
        "status": "upcoming",
    },
]

_ID_EXCLUDE = {"id"}


class _FakeBackend:
    """A ``BackendAdapter`` that returns a fixed payload, bypassing any model."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

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
        return ExtractResult(
            data=self._payload, system_fingerprint=None, deterministic=False
        )

    def supports(self, feature: str) -> bool:
        return False

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


class _Result:
    """Minimal stand-in for cliniq's ``ExtractionResult`` collection slots."""

    def __init__(self) -> None:
        self.medications: list[Any] = []
        self.appointments: list[Any] = []


class _Doc:
    """Minimal stand-in for cliniq's ``DocumentText`` (only full_text is read)."""

    def __init__(self, full_text: str) -> None:
        self.full_text = full_text


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    register_from_path(HERE / "medication.spec.yaml", registry=reg)
    register_from_path(HERE / "appointment.spec.yaml", registry=reg)
    return reg


def test_golden_medications_validate(registry: Registry) -> None:
    backend = _FakeBackend({"medications": _MEDICATIONS_GOLDEN})
    result = _Result()

    extract_medications(_Doc("stub"), backend, result, registry=registry)

    assert len(result.medications) == len(_MEDICATIONS_GOLDEN)
    for got, raw in zip(result.medications, _MEDICATIONS_GOLDEN, strict=True):
        from cliniq.schemas.medication import Medication

        expected = Medication.model_validate(raw).model_dump(exclude=_ID_EXCLUDE)
        assert got.model_dump(exclude=_ID_EXCLUDE) == expected


def test_golden_appointments_validate(registry: Registry) -> None:
    backend = _FakeBackend({"appointments": _APPOINTMENTS_GOLDEN})
    result = _Result()

    extract_appointments(_Doc("stub"), backend, result, registry=registry)

    assert len(result.appointments) == len(_APPOINTMENTS_GOLDEN)
    for got, raw in zip(result.appointments, _APPOINTMENTS_GOLDEN, strict=True):
        from cliniq.schemas.appointment import Appointment

        expected = Appointment.model_validate(raw).model_dump(exclude=_ID_EXCLUDE)
        assert got.model_dump(exclude=_ID_EXCLUDE) == expected
