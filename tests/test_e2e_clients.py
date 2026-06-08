"""End-to-end tests for the runnable scenario clients in ``examples/clients/``.

Two axes are covered:

1. **Golden artefacts** — the compiled prompt, JSON schema, and ``spec_hash``
   for each scenario spec are pinned under ``tests/golden/``. Any drift in the
   compiler output (or the specs) breaks these, which is the v2-change
   validation indicator. Regenerate intentionally with
   ``PROMPILER_REGEN_GOLDEN=1``.
2. **Client wiring** — each client module is executed end to end against its
   scripted backend payload to confirm the public API (``register_from_path``
   + ``run_sync``) still extracts the expected fields.

The medical scenario uses wholly synthetic, non-identifying data: the patient
reference ``PT-4471`` is a pseudonymous code, not a real medical record number,
and no field maps to a real person.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from prompiler import compile, run_sync
from prompiler.runtime.registry import register_from_path
from prompiler.spec import load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENTS_DIR = REPO_ROOT / "examples" / "clients"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"

if str(CLIENTS_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENTS_DIR))

import invoice_client  # noqa: E402
import medical_letter_client  # noqa: E402
import receipt_client  # noqa: E402
from _common import ScriptedBackend  # noqa: E402

_REGEN = os.environ.get("PROMPILER_REGEN_GOLDEN") == "1"

CLIENT_MODULES = {
    "invoice": invoice_client,
    "medical_letter": medical_letter_client,
    "receipt": receipt_client,
}


def _golden(name: str, ext: str, content: str) -> str:
    path = GOLDEN_DIR / f"{name}.{ext}"
    if _REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert path.exists(), (
        f"golden {path.name} missing; run with PROMPILER_REGEN_GOLDEN=1 to create it"
    )
    return path.read_text(encoding="utf-8")


@pytest.mark.e2e
@pytest.mark.parametrize("name", sorted(CLIENT_MODULES))
def test_compiled_artefacts_match_golden(name: str) -> None:
    spec_path = CLIENT_MODULES[name].SPEC_PATH
    bundle = compile(load_spec(spec_path))
    assert bundle.prompt == _golden(name, "prompt.txt", bundle.prompt)
    schema = json.dumps(bundle.pydantic_cls.model_json_schema(), indent=2, sort_keys=True)
    assert schema == _golden(name, "schema.json", schema)
    assert bundle.spec_hash == _golden(name, "hash", bundle.spec_hash).strip()


def _run_client(module: object):
    spec_path = module.SPEC_PATH  # type: ignore[attr-defined]
    backend = ScriptedBackend(module.SCRIPTED_PAYLOAD)  # type: ignore[attr-defined]
    register_from_path(str(spec_path))
    return run_sync(spec_path.stem, module.DOCUMENT, backend=backend)  # type: ignore[attr-defined]


@pytest.mark.e2e
def test_invoice_client_extracts_expected() -> None:
    result = _run_client(invoice_client)
    assert result.vendor_name == "ACME Office Supplies Ltd"
    assert str(result.total_amount) == "457.80"


@pytest.mark.e2e
def test_medical_letter_client_extracts_expected() -> None:
    result = _run_client(medical_letter_client)
    assert result.patient_reference == "PT-4471"
    assert result.referring_clinician == "Dr. Helen Marsh"
    assert "cardiology" in str(result.specialty)
    assert result.is_urgent is True
    assert len(result.medications) == 2
    assert result.medications[0].name == "Atorvastatin"


@pytest.mark.e2e
def test_receipt_client_extracts_expected() -> None:
    result = _run_client(receipt_client)
    assert result.merchant == "The Corner Bistro"
    assert len(result.line_items) == 3
    assert result.line_items[0].description == "Margherita pizza"
    assert result.line_items[0].quantity == 2
    assert str(result.total) == "49.74"
