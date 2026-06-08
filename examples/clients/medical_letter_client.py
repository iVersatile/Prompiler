#!/usr/bin/env python
"""Scenario client: extract referral details from a clinical letter.

The sample letter below is wholly synthetic and non-identifying: the patient
reference is a pseudonymous code (``PT-####``), not a real medical record
number, and no part of it maps to a real person.

Runs hermetically by default (scripted backend, no API key). Set
``PROMPILER_LIVE=1`` and ``ANTHROPIC_API_KEY`` to hit a real model instead.

    python examples/clients/medical_letter_client.py
"""

from __future__ import annotations

from pathlib import Path

from prompiler import run_sync
from prompiler.runtime.registry import register_from_path

from _common import select_backend

SPEC_PATH = Path(__file__).resolve().parents[1] / "medical_letter.yaml"

DOCUMENT = """\
RIVERSIDE COMMUNITY CLINIC
Referral letter — 14 February 2026

Patient reference: PT-4471

Dear Cardiology team,

I am referring the above patient for assessment of intermittent chest
discomfort on exertion, with a family history of ischaemic heart disease.
Please treat this referral as URGENT.

Current medications:
  - Atorvastatin 20mg once daily
  - Ramipril 5mg once daily

Kind regards,
Dr. Helen Marsh, General Practitioner
"""

# What a correct extraction looks like for the scripted (offline) run.
SCRIPTED_PAYLOAD = {
    "patient_reference": "PT-4471",
    "letter_date": "2026-02-14",
    "referring_clinician": "Dr. Helen Marsh",
    "specialty": "cardiology",
    "reason": "Intermittent chest discomfort on exertion with family history of ischaemic heart disease.",
    "is_urgent": True,
    "medications": [
        {"name": "Atorvastatin", "dose": "20mg once daily"},
        {"name": "Ramipril", "dose": "5mg once daily"},
    ],
}


def main() -> None:
    register_from_path(str(SPEC_PATH))
    backend = select_backend(SCRIPTED_PAYLOAD)
    result = run_sync("medical_letter", DOCUMENT, backend=backend)
    print("patient_reference  :", result.patient_reference)
    print("letter_date        :", result.letter_date)
    print("referring_clinician:", result.referring_clinician)
    print("specialty          :", result.specialty)
    print("is_urgent          :", result.is_urgent)
    print("medications        :", result.medications)


if __name__ == "__main__":
    main()
