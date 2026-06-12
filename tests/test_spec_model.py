"""Unit tests for prompiler.spec.model — EntitySpec / FieldSpec / Constraint / Label.

Covers PRD §5.1 (EntitySpec shape) + §5.2 (task semantics):
  - happy paths: invoice extract + email_category classify
  - top-level invariants: spec_version, task↔fields/labels exclusivity, extra=forbid
  - FieldSpec per-type invariants: enum requires values, array requires item,
    object requires fields, pattern only on string, min/max only on numeric, etc.
  - recursion: nested object inside array
  - Constraint default severity = warn; rejects bad severity
  - Label minimal shape
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from prompiler.spec.model import Constraint, EntitySpec, FieldSpec, Label

# ---------------------------------------------------------------------------
# Fixtures (synthetic; mirrors PRD §5 examples)
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


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoice_extract_spec_parses() -> None:
    spec = EntitySpec.model_validate(_invoice_spec())
    assert spec.name == "invoice"
    assert spec.task == "extract"
    assert spec.cite is True
    assert spec.fields is not None
    assert len(spec.fields) == 5
    assert spec.labels is None
    assert len(spec.cross_field_constraints) == 1


@pytest.mark.unit
def test_email_classify_spec_parses() -> None:
    spec = EntitySpec.model_validate(_email_category_spec())
    assert spec.name == "email_category"
    assert spec.task == "classify"
    assert spec.labels is not None
    assert len(spec.labels) == 4
    assert spec.fields is None
    assert spec.allow_multi_label is False


# ---------------------------------------------------------------------------
# Top-level invariants
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_version_two_accepted() -> None:
    data = _invoice_spec()
    data["spec_version"] = 2
    spec = EntitySpec.model_validate(data)
    assert spec.spec_version == 2


@pytest.mark.unit
def test_spec_version_one_rejected() -> None:
    data = _invoice_spec()
    data["spec_version"] = 1
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_unknown_top_level_key_rejected() -> None:
    data = _invoice_spec()
    data["bogus"] = "x"
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_extract_without_fields_rejected() -> None:
    data = _invoice_spec()
    data.pop("fields")
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_extract_with_labels_rejected() -> None:
    data = _invoice_spec()
    data["labels"] = [{"name": "x"}]
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_classify_without_labels_rejected() -> None:
    data = _email_category_spec()
    data.pop("labels")
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_classify_with_fields_rejected() -> None:
    data = _email_category_spec()
    data["fields"] = [{"name": "x", "type": "string"}]
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


# ---------------------------------------------------------------------------
# extends inheritance reference (E2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extends_absent_defaults_to_none() -> None:
    spec = EntitySpec.model_validate(_invoice_spec())
    assert spec.extends is None


@pytest.mark.unit
def test_extends_string_reference_accepted() -> None:
    data = _invoice_spec()
    data["extends"] = "base_invoice"
    spec = EntitySpec.model_validate(data)
    assert spec.extends == "base_invoice"


@pytest.mark.unit
def test_extends_empty_string_rejected() -> None:
    data = _invoice_spec()
    data["extends"] = ""
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_extends_whitespace_only_rejected() -> None:
    data = _invoice_spec()
    data["extends"] = "   "
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


# ---------------------------------------------------------------------------
# FieldSpec per-type invariants
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_requires_values() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "enum"})


@pytest.mark.unit
def test_enum_values_must_be_unique_and_nonempty() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "enum", "values": []})
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "enum", "values": ["a", "a"]})


@pytest.mark.unit
def test_array_requires_item() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "array"})


@pytest.mark.unit
def test_object_requires_nonempty_fields() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "object"})
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "object", "fields": []})


@pytest.mark.unit
def test_pattern_only_allowed_on_string() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "integer", "pattern": "^[0-9]+$"})


@pytest.mark.unit
def test_values_only_allowed_on_enum() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "string", "values": ["a"]})


@pytest.mark.unit
def test_item_only_allowed_on_array() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "string", "item": {"type": "string"}})


@pytest.mark.unit
def test_fields_only_allowed_on_object() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate(
            {"name": "x", "type": "string", "fields": [{"name": "y", "type": "string"}]}
        )


@pytest.mark.unit
def test_minmax_only_allowed_on_numeric() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "string", "minimum": 0})
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "boolean", "maximum": 1})
    # ok on integer/decimal
    FieldSpec.model_validate({"name": "x", "type": "integer", "minimum": 0, "maximum": 10})
    FieldSpec.model_validate({"name": "x", "type": "decimal", "minimum": 0.0})


@pytest.mark.unit
def test_unknown_field_key_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"name": "x", "type": "string", "bogus": 1})


@pytest.mark.unit
def test_field_required_defaults_true() -> None:
    f = FieldSpec.model_validate({"name": "x", "type": "string"})
    assert f.required is True


# ---------------------------------------------------------------------------
# Recursion (object inside array)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_nested_object_in_array_parses() -> None:
    data = {
        "name": "line_items",
        "type": "array",
        "item": {
            "type": "object",
            "fields": [
                {"name": "description", "type": "string", "required": True},
                {"name": "quantity", "type": "decimal", "required": True},
            ],
        },
    }
    f = FieldSpec.model_validate(data)
    assert f.type == "array"
    assert f.item is not None
    assert f.item.type == "object"
    assert f.item.fields is not None
    assert len(f.item.fields) == 2


@pytest.mark.unit
def test_array_item_has_no_name() -> None:
    # name is optional on item/nested anonymous shapes
    f = FieldSpec.model_validate({"name": "tags", "type": "array", "item": {"type": "string"}})
    assert f.item is not None
    assert f.item.name is None


# ---------------------------------------------------------------------------
# Constraint + Label
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_constraint_severity_defaults_to_warn() -> None:
    c = Constraint.model_validate({"expr": "a == b"})
    assert c.severity == "warn"


@pytest.mark.unit
def test_constraint_severity_error_accepted() -> None:
    c = Constraint.model_validate({"expr": "a == b", "severity": "error"})
    assert c.severity == "error"


@pytest.mark.unit
def test_constraint_severity_rejected_value() -> None:
    with pytest.raises(ValidationError):
        Constraint.model_validate({"expr": "a == b", "severity": "fatal"})


@pytest.mark.unit
def test_label_minimal_parses() -> None:
    label = Label.model_validate({"name": "billing"})
    assert label.name == "billing"
    assert label.description is None


# ---------------------------------------------------------------------------
# Multi-modal input declaration (A1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_input_modalities_defaults_to_text() -> None:
    spec = EntitySpec.model_validate(_invoice_spec())
    assert spec.input_modalities == ["text"]


@pytest.mark.unit
def test_input_modalities_accepts_image_and_audio() -> None:
    data = _invoice_spec()
    data["input_modalities"] = ["text", "image", "audio"]
    spec = EntitySpec.model_validate(data)
    assert spec.input_modalities == ["text", "image", "audio"]


@pytest.mark.unit
def test_input_modalities_image_only_spec_loads() -> None:
    data = _invoice_spec()
    data["input_modalities"] = ["image"]
    spec = EntitySpec.model_validate(data)
    assert spec.input_modalities == ["image"]


@pytest.mark.unit
def test_input_modalities_empty_rejected() -> None:
    data = _invoice_spec()
    data["input_modalities"] = []
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_input_modalities_duplicate_rejected() -> None:
    data = _invoice_spec()
    data["input_modalities"] = ["text", "text"]
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)


@pytest.mark.unit
def test_input_modalities_unknown_value_rejected() -> None:
    data = _invoice_spec()
    data["input_modalities"] = ["video"]
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(data)
