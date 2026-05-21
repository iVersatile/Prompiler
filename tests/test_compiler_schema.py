"""Unit tests for prompiler.compiler.jsonschema_emit — JSON Schema emitter (P1.8).

Covers PLAN.md L99 scope (verbatim):
  "JSON Schema emitter from synthesised Pydantic model."

Contract anchored in architecture.md L198 (verbatim):
  "JSON Schema emitter. Pydantic v2's native ``model_json_schema()`` with
  explicit draft 2020-12 selection. Output passes a JSON-Schema-meta
  validator in unit tests."

Canonical path (architecture.md L63): ``compiler/jsonschema_emit.py``.

Determinism: PRD FR-2 / PLAN.md L107 — same spec + same prompiler version =>
identical schema. Byte-canonicalisation is the bundle stage's job (P1.9);
this layer asserts dict equality only.

Pydantic v2 trait notes (verified against pydantic>=2.9):
  - top-level object emits ``additionalProperties: false`` from
    ``ConfigDict(extra="forbid")``.
  - nested objects land in ``$defs`` with ``$ref`` at the usage site.
  - optional scalar fields surface as ``anyOf`` containing ``{"type": "null"}``
    plus ``default: null``.
  - enum-valued fields surface as ``enum: [...]`` with ``type: string``.
  - ``Decimal`` may surface as ``anyOf`` of ``number`` + ``string`` pattern,
    or as a direct ``number`` — both shapes are tolerated below.
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from prompiler.compiler import synthesize_schema
from prompiler.spec import EntitySpec

DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"


def _load(data: dict[str, Any]) -> EntitySpec:
    return EntitySpec.model_validate(data)


def _extract_spec(**overrides: Any) -> EntitySpec:
    base: dict[str, Any] = {
        "spec_version": 1,
        "name": "invoice",
        "task": "extract",
        "description": "Extract invoice fields from a scanned receipt.",
        "fields": [
            {
                "name": "invoice_number",
                "type": "string",
                "required": True,
                "pattern": r"^[A-Z0-9-]{3,32}$",
            },
            {
                "name": "total_amount",
                "type": "decimal",
                "required": True,
                "minimum": 0,
            },
            {
                "name": "currency",
                "type": "enum",
                "required": True,
                "values": ["USD", "EUR"],
            },
            {
                "name": "notes",
                "type": "string",
                "required": False,
            },
            {
                "name": "billing",
                "type": "object",
                "required": True,
                "fields": [
                    {"name": "city", "type": "string", "required": True},
                    {"name": "zip", "type": "string", "required": True},
                ],
            },
        ],
    }
    base.update(overrides)
    return _load(base)


def _classify_spec(**overrides: Any) -> EntitySpec:
    base: dict[str, Any] = {
        "spec_version": 1,
        "name": "email_category",
        "task": "classify",
        "description": "Classify customer email by intent.",
        "labels": [
            {"name": "billing", "description": "Billing or payment questions."},
            {"name": "support", "description": "Technical support requests."},
            {"name": "other", "description": "Anything else."},
        ],
    }
    base.update(overrides)
    return _load(base)


def _props(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    assert isinstance(props, dict), f"schema missing top-level properties: {schema}"
    return props


@pytest.mark.unit
def test_returns_dict() -> None:
    schema = synthesize_schema(_extract_spec())
    assert isinstance(schema, dict)


@pytest.mark.unit
def test_declares_draft_2020_12_schema_uri() -> None:
    schema = synthesize_schema(_extract_spec())
    assert schema.get("$schema") == DRAFT_2020_12_URI


@pytest.mark.unit
def test_extract_schema_passes_meta_validator() -> None:
    schema = synthesize_schema(_extract_spec())
    Draft202012Validator.check_schema(schema)


@pytest.mark.unit
def test_classify_single_label_schema_passes_meta_validator() -> None:
    schema = synthesize_schema(_classify_spec(allow_multi_label=False))
    Draft202012Validator.check_schema(schema)


@pytest.mark.unit
def test_classify_multi_label_schema_passes_meta_validator() -> None:
    schema = synthesize_schema(_classify_spec(allow_multi_label=True))
    Draft202012Validator.check_schema(schema)


@pytest.mark.unit
def test_extract_top_level_is_object() -> None:
    schema = synthesize_schema(_extract_spec())
    assert schema.get("type") == "object"


@pytest.mark.unit
def test_extract_properties_list_every_field() -> None:
    schema = synthesize_schema(_extract_spec())
    props = _props(schema)
    for name in ("invoice_number", "total_amount", "currency", "notes", "billing"):
        assert name in props, f"missing property {name!r} in {sorted(props)}"


@pytest.mark.unit
def test_extract_required_lists_required_fields_only() -> None:
    schema = synthesize_schema(_extract_spec())
    required = set(schema.get("required", []))
    assert {"invoice_number", "total_amount", "currency", "billing"} <= required
    assert "notes" not in required


@pytest.mark.unit
def test_extract_forbids_additional_properties() -> None:
    schema = synthesize_schema(_extract_spec())
    assert schema.get("additionalProperties") is False


@pytest.mark.unit
def test_string_pattern_constraint_emitted() -> None:
    schema = synthesize_schema(_extract_spec())
    invoice_number = _props(schema)["invoice_number"]
    assert invoice_number.get("pattern") == r"^[A-Z0-9-]{3,32}$"


@pytest.mark.unit
def test_enum_field_emits_enum_keyword() -> None:
    schema = synthesize_schema(_extract_spec())
    currency = _props(schema)["currency"]
    assert set(currency.get("enum", [])) == {"USD", "EUR"}


@pytest.mark.unit
def test_optional_field_admits_null() -> None:
    """Pydantic v2 emits ``anyOf`` with a ``{"type": "null"}`` branch for
    optional scalar fields, plus ``default: null``. Either shape is acceptable
    as long as null is a legal value."""
    schema = synthesize_schema(_extract_spec())
    notes = _props(schema)["notes"]
    any_of = notes.get("anyOf")
    if any_of is not None:
        assert any(branch.get("type") == "null" for branch in any_of), notes
    else:
        # Direct shape: type may be a list including "null".
        type_field = notes.get("type")
        if isinstance(type_field, list):
            assert "null" in type_field, notes
        else:
            # Fallback: a "default" of None at minimum indicates optionality.
            assert "default" in notes, notes


@pytest.mark.unit
def test_nested_object_field_is_object_typed() -> None:
    """The ``billing`` field is an object — it may be inlined or referenced via
    ``$defs``. Either form must ultimately type as ``object``."""
    schema = synthesize_schema(_extract_spec())
    billing = _props(schema)["billing"]
    if "$ref" in billing:
        ref = billing["$ref"]
        assert ref.startswith("#/$defs/"), ref
        defs = schema.get("$defs", {})
        target = defs[ref.removeprefix("#/$defs/")]
        assert target.get("type") == "object"
        nested_props = target.get("properties", {})
        assert {"city", "zip"} <= set(nested_props)
    else:
        assert billing.get("type") == "object"
        nested_props = billing.get("properties", {})
        assert {"city", "zip"} <= set(nested_props)


@pytest.mark.unit
def test_decimal_field_is_numeric_or_string_decimal() -> None:
    """Pydantic v2 may surface ``Decimal`` as ``anyOf`` of ``number`` + a
    ``string`` pattern, or as a direct ``number``. Both are tolerated."""
    schema = synthesize_schema(_extract_spec())
    total = _props(schema)["total_amount"]
    any_of = total.get("anyOf")
    if any_of is not None:
        types = {branch.get("type") for branch in any_of}
        assert "number" in types or "string" in types, total
    else:
        assert total.get("type") in {"number", "string"}, total


@pytest.mark.unit
def test_classify_single_label_emits_string_enum() -> None:
    schema = synthesize_schema(_classify_spec(allow_multi_label=False))
    props = _props(schema)
    assert "label" in props
    label = props["label"]
    label_enum = label.get("enum")
    if label_enum is None:
        ref = label.get("$ref", "")
        assert ref.startswith("#/$defs/"), label
        target = schema["$defs"][ref.removeprefix("#/$defs/")]
        label_enum = target.get("enum")
    assert set(label_enum or []) == {"billing", "support", "other"}


@pytest.mark.unit
def test_classify_multi_label_emits_array_of_enum() -> None:
    schema = synthesize_schema(_classify_spec(allow_multi_label=True))
    props = _props(schema)
    assert "labels" in props
    labels = props["labels"]
    assert labels.get("type") == "array"
    items = labels.get("items", {})
    items_enum = items.get("enum")
    if items_enum is None:
        ref = items.get("$ref", "")
        assert ref.startswith("#/$defs/"), items
        target = schema["$defs"][ref.removeprefix("#/$defs/")]
        items_enum = target.get("enum")
    assert set(items_enum or []) == {"billing", "support", "other"}


@pytest.mark.unit
def test_classify_omits_extract_only_properties() -> None:
    schema = synthesize_schema(_classify_spec())
    props = _props(schema)
    assert "invoice_number" not in props
    assert "total_amount" not in props


@pytest.mark.unit
def test_determinism_extract_dict_equal() -> None:
    spec = _extract_spec()
    a = synthesize_schema(spec)
    b = synthesize_schema(spec)
    assert a == b


@pytest.mark.unit
def test_determinism_classify_dict_equal() -> None:
    spec = _classify_spec(allow_multi_label=True)
    a = synthesize_schema(spec)
    b = synthesize_schema(spec)
    assert a == b


@pytest.mark.unit
def test_schema_uri_appears_first_or_is_present() -> None:
    """``$schema`` must be present at the top level. Position is not
    semantically meaningful, but emitting it as the first key is the standard
    convention and aids byte-canonical bundling downstream."""
    schema = synthesize_schema(_extract_spec())
    assert "$schema" in schema


@pytest.mark.unit
def test_emitter_does_not_mutate_input_spec() -> None:
    spec = _extract_spec()
    before = spec.model_dump()
    synthesize_schema(spec)
    after = spec.model_dump()
    assert before == after
