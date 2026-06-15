"""DRAFT — prompiler-backed rewrite of cliniq's ``extract_appointments``.

Mirrors ``extract_medications_prompiler.py``: replaces the hand-rolled prompt +
``adapter.complete_json`` call in
``cliniq/src/cliniq/extraction/prompts/appointment.py`` with a single
``run_sync("appointment", ...)`` against the compiled ``appointment.spec.yaml``.

``run_sync`` returns one ``BaseModel`` whose top-level ``appointments`` field is
the list — we read ``model_dump()["appointments"]`` and re-validate each item
through cliniq's ``Appointment`` model, keeping the per-item ValidationError skip.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from cliniq.schemas.appointment import Appointment
from prompiler.runtime import PrompilerError, run_sync

if TYPE_CHECKING:
    from cliniq.extraction.result import ExtractionResult
    from cliniq.ingestion.pdf_reader import DocumentText
    from prompiler.backends.base import BackendAdapter
    from prompiler.runtime.registry import Registry

log = logging.getLogger(__name__)


def extract_appointments(
    doc: DocumentText,
    backend: BackendAdapter,
    result: ExtractionResult,
    *,
    registry: Registry | None = None,
) -> None:
    try:
        extracted = run_sync(
            "appointment", doc.full_text, backend=backend, registry=registry
        )
    except PrompilerError as exc:
        log.warning("extract_appointments: extraction failed (%s)", exc)
        return
    for item in extracted.model_dump().get("appointments", []):
        try:
            result.appointments.append(Appointment.model_validate(item))
        except ValidationError as exc:
            log.warning("extract_appointments: skipping item — parse failed (%s)", exc)
