"""DRAFT — prompiler-backed rewrite of cliniq's ``extract_medications``.

Replaces the hand-rolled ``_SYSTEM`` prompt + ``adapter.complete_json`` call in
``cliniq/src/cliniq/extraction/prompts/medication.py`` with a single
``run_sync("medication", ...)`` against the compiled ``medication.spec.yaml``.

``run_sync`` returns ONE ``BaseModel`` whose top-level ``medications`` field is
the list — we read ``model_dump()["medications"]`` and re-validate each item
through cliniq's ``Medication`` model, preserving the original per-item
ValidationError-skip behaviour so one malformed row never drops the whole batch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from cliniq.schemas.medication import Medication
from prompiler.runtime import PrompilerError, run_sync

if TYPE_CHECKING:
    from cliniq.extraction.result import ExtractionResult
    from cliniq.ingestion.pdf_reader import DocumentText
    from prompiler.backends.base import BackendAdapter
    from prompiler.runtime.registry import Registry

log = logging.getLogger(__name__)


def extract_medications(
    doc: DocumentText,
    backend: BackendAdapter,
    result: ExtractionResult,
    *,
    registry: Registry | None = None,
) -> None:
    try:
        extracted = run_sync(
            "medication", doc.full_text, backend=backend, registry=registry
        )
    except PrompilerError as exc:
        log.warning("extract_medications: extraction failed (%s)", exc)
        return
    for item in extracted.model_dump().get("medications", []):
        try:
            result.medications.append(Medication.model_validate(item))
        except ValidationError as exc:
            log.warning("extract_medications: skipping item — parse failed (%s)", exc)
