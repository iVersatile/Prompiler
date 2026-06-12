"""Unit tests for prompiler.spec.hash — canonical_yaml + spec_hash (P1.3).

Covers RULES.md §10 + PRD §5:
  - hex64 SHA-256 format + determinism
  - field-level sensitivity (name, description, constraint expr)
  - source-YAML top-level key reorder = same hash (canonical sort)
  - fields / labels list reorder = different hash (positional semantics)
  - COMPILER_PROTOCOL_VERSION bump = different hash (monkeypatched)
  - canonical_yaml: sorted keys, deterministic across calls
  - both PRD §5 fixtures hash; distinct specs -> distinct hashes
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from prompiler.spec import EntitySpec, canonical_yaml, load_spec, spec_hash

# ---------------------------------------------------------------------------
# Fixtures (synthetic; mirror PRD §5 examples — same shape as test_spec_loader)
# ---------------------------------------------------------------------------


def _invoice_spec() -> dict[str, Any]:
    return {
        "spec_version": 2,
        "name": "invoice",
        "task": "extract",
        "description": "Extract billing details from a single invoice document.",
        "cite": True,
        "fields": [
            {
                "name": "vendor_name",
                "type": "string",
                "required": True,
                "description": "Legal name of the issuing vendor.",
            },
            {
                "name": "invoice_number",
                "type": "string",
                "required": True,
                "pattern": "^[A-Z0-9-]{3,32}$",
            },
            {"name": "issue_date", "type": "date", "required": True},
            {"name": "total_amount", "type": "decimal", "required": True},
            {
                "name": "currency",
                "type": "enum",
                "values": ["USD", "EUR", "GBP", "JPY", "CHF"],
                "required": True,
            },
        ],
        "cross_field_constraints": [
            {"expr": "sum(line_items.line_total) == total_amount", "severity": "warn"}
        ],
    }


def _email_category_spec() -> dict[str, Any]:
    return {
        "spec_version": 2,
        "name": "email_category",
        "task": "classify",
        "description": "Route inbound support email into one routing bucket.",
        "labels": [
            {"name": "billing", "description": "Payments, invoices, refunds."},
            {"name": "technical", "description": "Product not working."},
            {"name": "sales", "description": "Pre-purchase questions."},
            {"name": "spam", "description": "Unsolicited promotion."},
        ],
        "allow_multi_label": False,
    }


def _write_yaml(tmp_path: Path, name: str, data: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, name: str, data: dict[str, Any]) -> EntitySpec:
    return load_spec(_write_yaml(tmp_path, name, data))


# ---------------------------------------------------------------------------
# Hash format + determinism
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_format_is_hex64(tmp_path: Path) -> None:
    spec = _load(tmp_path, "invoice.yaml", _invoice_spec())
    h = spec_hash(spec)
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


@pytest.mark.unit
def test_spec_hash_is_deterministic(tmp_path: Path) -> None:
    spec_a = _load(tmp_path, "a.yaml", _invoice_spec())
    spec_b = _load(tmp_path, "b.yaml", _invoice_spec())
    assert spec_hash(spec_a) == spec_hash(spec_b)


@pytest.mark.unit
def test_spec_hash_stable_across_calls(tmp_path: Path) -> None:
    spec = _load(tmp_path, "invoice.yaml", _invoice_spec())
    assert spec_hash(spec) == spec_hash(spec)


# ---------------------------------------------------------------------------
# Field-level sensitivity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_changes_with_name(tmp_path: Path) -> None:
    base = _load(tmp_path, "base.yaml", _invoice_spec())
    mutated_data = _invoice_spec()
    mutated_data["name"] = "invoice_v2"
    mutated = _load(tmp_path, "mutated.yaml", mutated_data)
    assert spec_hash(base) != spec_hash(mutated)


@pytest.mark.unit
def test_spec_hash_changes_with_field_description(tmp_path: Path) -> None:
    base = _load(tmp_path, "base.yaml", _invoice_spec())
    mutated_data = _invoice_spec()
    mutated_data["fields"][0]["description"] = "Different vendor description."
    mutated = _load(tmp_path, "mutated.yaml", mutated_data)
    assert spec_hash(base) != spec_hash(mutated)


@pytest.mark.unit
def test_spec_hash_changes_with_constraint_expr(tmp_path: Path) -> None:
    base = _load(tmp_path, "base.yaml", _invoice_spec())
    mutated_data = _invoice_spec()
    mutated_data["cross_field_constraints"][0]["expr"] = "total_amount > 0"
    mutated = _load(tmp_path, "mutated.yaml", mutated_data)
    assert spec_hash(base) != spec_hash(mutated)


# ---------------------------------------------------------------------------
# Order semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_invariant_under_source_key_reorder(tmp_path: Path) -> None:
    """Top-level YAML key order must not affect hash (canonical sort flattens it)."""
    data = _invoice_spec()
    reordered: dict[str, Any] = {
        "task": data["task"],
        "name": data["name"],
        "spec_version": data["spec_version"],
        "cite": data["cite"],
        "description": data["description"],
        "cross_field_constraints": data["cross_field_constraints"],
        "fields": data["fields"],
    }
    h_original = spec_hash(_load(tmp_path, "original.yaml", data))
    h_reordered = spec_hash(_load(tmp_path, "reordered.yaml", reordered))
    assert h_original == h_reordered


@pytest.mark.unit
def test_spec_hash_changes_with_fields_reorder(tmp_path: Path) -> None:
    """List-element order is semantic — reversing `fields` MUST change the hash."""
    base = _load(tmp_path, "base.yaml", _invoice_spec())
    mutated_data = _invoice_spec()
    mutated_data["fields"] = list(reversed(mutated_data["fields"]))
    mutated = _load(tmp_path, "mutated.yaml", mutated_data)
    assert spec_hash(base) != spec_hash(mutated)


@pytest.mark.unit
def test_spec_hash_changes_with_labels_reorder(tmp_path: Path) -> None:
    base = _load(tmp_path, "base.yaml", _email_category_spec())
    mutated_data = _email_category_spec()
    mutated_data["labels"] = list(reversed(mutated_data["labels"]))
    mutated = _load(tmp_path, "mutated.yaml", mutated_data)
    assert spec_hash(base) != spec_hash(mutated)


# ---------------------------------------------------------------------------
# Protocol-version sensitivity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_changes_with_protocol_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bumping COMPILER_PROTOCOL_VERSION MUST invalidate the hash."""
    spec = _load(tmp_path, "invoice.yaml", _invoice_spec())
    h_v1 = spec_hash(spec)
    monkeypatch.setattr("prompiler.spec.hash.COMPILER_PROTOCOL_VERSION", "999")
    h_v2 = spec_hash(spec)
    assert h_v1 != h_v2


# ---------------------------------------------------------------------------
# canonical_yaml contracts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_canonical_yaml_sorts_top_level_keys(tmp_path: Path) -> None:
    spec = _load(tmp_path, "invoice.yaml", _invoice_spec())
    text = canonical_yaml(spec)
    parsed = yaml.safe_load(text)
    keys = list(parsed.keys())
    assert keys == sorted(keys)


@pytest.mark.unit
def test_canonical_yaml_is_deterministic(tmp_path: Path) -> None:
    spec = _load(tmp_path, "invoice.yaml", _invoice_spec())
    assert canonical_yaml(spec) == canonical_yaml(spec)


# ---------------------------------------------------------------------------
# Modal-field sensitivity (A2): input_modalities folds into the digest
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_changes_when_modal_field_added(tmp_path: Path) -> None:
    """A text-only spec and an image-enabled spec MUST hash differently."""
    text_spec = _load(tmp_path, "text.yaml", _invoice_spec())
    modal_data = _invoice_spec()
    modal_data["input_modalities"] = ["text", "image"]
    modal_spec = _load(tmp_path, "modal.yaml", modal_data)
    assert spec_hash(text_spec) != spec_hash(modal_spec)


@pytest.mark.unit
def test_spec_hash_changes_when_modal_field_removed(tmp_path: Path) -> None:
    """Dropping a declared modality back to the default MUST change the hash."""
    modal_data = _invoice_spec()
    modal_data["input_modalities"] = ["text", "audio"]
    modal_spec = _load(tmp_path, "modal.yaml", modal_data)
    text_spec = _load(tmp_path, "text.yaml", _invoice_spec())
    assert spec_hash(modal_spec) != spec_hash(text_spec)


@pytest.mark.unit
def test_spec_hash_invariant_when_modalities_match(tmp_path: Path) -> None:
    """Explicit default modalities hash identically to the elided default."""
    explicit_data = _invoice_spec()
    explicit_data["input_modalities"] = ["text"]
    explicit_spec = _load(tmp_path, "explicit.yaml", explicit_data)
    elided_spec = _load(tmp_path, "elided.yaml", _invoice_spec())
    assert spec_hash(explicit_spec) == spec_hash(elided_spec)


# ---------------------------------------------------------------------------
# PRD §5 fixtures both hash; distinct specs -> distinct hashes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_works_for_email_category(tmp_path: Path) -> None:
    spec = _load(tmp_path, "email_category.yaml", _email_category_spec())
    h = spec_hash(spec)
    assert len(h) == 64


@pytest.mark.unit
def test_spec_hash_distinct_across_specs(tmp_path: Path) -> None:
    invoice = _load(tmp_path, "invoice.yaml", _invoice_spec())
    email = _load(tmp_path, "email_category.yaml", _email_category_spec())
    assert spec_hash(invoice) != spec_hash(email)
