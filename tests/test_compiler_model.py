"""Unit tests for prompiler.compiler.model — Pydantic model synthesiser (P1.5).

Covers PLAN.md L96 scope (verbatim):
  "Pydantic model synthesiser (`pydantic.create_model`) supporting:
   string, integer, decimal, boolean, date, datetime, enum, array,
   object (nested), optional."

Public API under test:

    def synthesize_model(spec: EntitySpec) -> type[BaseModel]: ...

Returns a freshly-built ``BaseModel`` subclass whose field annotations,
constraints, and nested shape derive from the EntitySpec. Same spec ->
equivalent JSON Schema (acceptance criterion PLAN.md L107).

Classify task (PRD §5): single-label spec produces a model with one
``label`` field annotated ``Literal[<names>]``; multi-label spec produces
a ``labels: list[Literal[<names>]]`` field.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from prompiler.compiler import synthesize_model
from prompiler.spec import EntitySpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(data: dict[str, Any]) -> EntitySpec:
    return EntitySpec.model_validate(data)


def _extract_spec(fields: list[dict[str, Any]], name: str = "thing") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": name,
        "task": "extract",
        "description": "Synthetic extract spec used by synthesiser tests.",
        "fields": fields,
    }


def _invoice_spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
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
                "description": "Vendor-issued invoice identifier.",
            },
            {
                "name": "issue_date",
                "type": "date",
                "required": True,
                "description": "Date the invoice was issued.",
            },
            {
                "name": "total_amount",
                "type": "decimal",
                "required": True,
                "description": "Total amount due, in the stated currency.",
            },
            {
                "name": "currency",
                "type": "enum",
                "values": ["USD", "EUR", "GBP", "JPY", "CHF"],
                "required": True,
                "description": "ISO-4217 currency code for the invoice total.",
            },
        ],
    }


def _email_category_spec(multi: bool = False) -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "email_category",
        "task": "classify",
        "description": "Route inbound support email into one routing bucket.",
        "labels": [
            {"name": "billing", "description": "Payments, invoices, refunds."},
            {"name": "technical", "description": "Product not working."},
            {"name": "sales", "description": "Pre-purchase questions."},
            {"name": "spam", "description": "Unsolicited promotion."},
        ],
        "allow_multi_label": multi,
    }


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_synthesize_returns_basemodel_subclass() -> None:
    spec = _load(_invoice_spec())
    Model = synthesize_model(spec)
    assert isinstance(Model, type)
    assert issubclass(Model, BaseModel)


@pytest.mark.unit
def test_synthesize_classname_is_pascalcase_of_spec_name() -> None:
    spec = _load(
        {
            "spec_version": 1,
            "name": "email_category_v2",
            "task": "classify",
            "description": "Two-bucket classifier.",
            "labels": [
                {"name": "a", "description": "first bucket"},
                {"name": "b", "description": "second bucket"},
            ],
        }
    )
    Model = synthesize_model(spec)
    assert Model.__name__ == "EmailCategoryV2"


# ---------------------------------------------------------------------------
# Scalar types
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_string_field_round_trips() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "x", "type": "string", "description": "a string"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(x="hello")
    assert instance.x == "hello"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_integer_field_round_trips() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "n", "type": "integer", "description": "an integer"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(n=42)
    assert instance.n == 42  # type: ignore[attr-defined]


@pytest.mark.unit
def test_decimal_field_round_trips() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "amount", "type": "decimal", "description": "money"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(amount="12.34")
    assert instance.amount == Decimal("12.34")  # type: ignore[attr-defined]


@pytest.mark.unit
def test_boolean_field_round_trips() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "flag", "type": "boolean", "description": "a flag"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(flag=True)
    assert instance.flag is True  # type: ignore[attr-defined]


@pytest.mark.unit
def test_date_field_round_trips() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "d", "type": "date", "description": "a date"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(d="2026-03-05")
    assert instance.d == _dt.date(2026, 3, 5)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_datetime_field_round_trips() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "ts", "type": "datetime", "description": "a timestamp"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(ts="2026-03-05T12:34:56+00:00")
    assert instance.ts == _dt.datetime(  # type: ignore[attr-defined]
        2026, 3, 5, 12, 34, 56, tzinfo=_dt.UTC
    )


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_string_pattern_enforced() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "invoice_number",
                    "type": "string",
                    "pattern": "^[A-Z0-9-]{3,32}$",
                    "description": "An invoice number.",
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    Model(invoice_number="INV-001")
    with pytest.raises(ValidationError):
        Model(invoice_number="lowercase")


@pytest.mark.unit
def test_integer_minimum_maximum_enforced() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "score",
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "A score in [0,100].",
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    Model(score=0)
    Model(score=100)
    with pytest.raises(ValidationError):
        Model(score=-1)
    with pytest.raises(ValidationError):
        Model(score=101)


@pytest.mark.unit
def test_decimal_minimum_maximum_enforced() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "ratio",
                    "type": "decimal",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "A ratio in [0,1].",
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    Model(ratio="0.5")
    with pytest.raises(ValidationError):
        Model(ratio="1.5")


@pytest.mark.unit
def test_enum_accepts_listed_value_rejects_other() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "currency",
                    "type": "enum",
                    "values": ["USD", "EUR", "GBP"],
                    "description": "ISO-4217 code.",
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    Model(currency="USD")
    with pytest.raises(ValidationError):
        Model(currency="ZZZ")


# ---------------------------------------------------------------------------
# required / optional
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_required_field_missing_raises() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "x", "type": "string", "required": True, "description": "req"}],
        )
    )
    Model = synthesize_model(spec)
    with pytest.raises(ValidationError):
        Model()


@pytest.mark.unit
def test_optional_field_defaults_to_none_when_missing() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "x", "type": "string", "required": False, "description": "opt"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model()
    assert instance.x is None  # type: ignore[attr-defined]


@pytest.mark.unit
def test_optional_field_accepts_explicit_none() -> None:
    spec = _load(
        _extract_spec(
            [{"name": "x", "type": "string", "required": False, "description": "opt"}],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(x=None)
    assert instance.x is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# array
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_array_of_strings_validates_each_item() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "tags",
                    "type": "array",
                    "description": "free-form tags",
                    "item": {"type": "string"},
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(tags=["alpha", "beta"])
    assert instance.tags == ["alpha", "beta"]  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        Model(tags=[123])


@pytest.mark.unit
def test_array_of_objects_validates_nested_shape() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "line_items",
                    "type": "array",
                    "description": "invoice line items",
                    "item": {
                        "type": "object",
                        "fields": [
                            {
                                "name": "sku",
                                "type": "string",
                                "description": "stock-keeping unit",
                            },
                            {
                                "name": "qty",
                                "type": "integer",
                                "description": "quantity",
                            },
                        ],
                    },
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(line_items=[{"sku": "A-1", "qty": 2}])
    assert instance.line_items[0].sku == "A-1"  # type: ignore[attr-defined]
    assert instance.line_items[0].qty == 2  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        Model(line_items=[{"sku": "A-1"}])  # missing required qty


# ---------------------------------------------------------------------------
# nested object
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_nested_object_validates_recursively() -> None:
    spec = _load(
        _extract_spec(
            [
                {
                    "name": "billing_address",
                    "type": "object",
                    "description": "billing address block",
                    "fields": [
                        {"name": "city", "type": "string", "description": "city"},
                        {"name": "zip", "type": "string", "description": "postal code"},
                    ],
                },
            ],
        )
    )
    Model = synthesize_model(spec)
    instance = Model(billing_address={"city": "Berlin", "zip": "10115"})
    assert instance.billing_address.city == "Berlin"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        Model(billing_address={"city": "Berlin"})  # missing zip


# ---------------------------------------------------------------------------
# classify task
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classify_single_label_model_has_literal_label_field() -> None:
    Model = synthesize_model(_load(_email_category_spec(multi=False)))
    instance = Model(label="billing")
    assert instance.label == "billing"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        Model(label="other")


@pytest.mark.unit
def test_classify_multi_label_model_has_labels_list_field() -> None:
    Model = synthesize_model(_load(_email_category_spec(multi=True)))
    instance = Model(labels=["billing", "sales"])
    assert instance.labels == ["billing", "sales"]  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        Model(labels=["other"])


# ---------------------------------------------------------------------------
# determinism + PRD §5 round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_same_spec_produces_equivalent_json_schema() -> None:
    spec = _load(_invoice_spec())
    a = synthesize_model(spec).model_json_schema()
    b = synthesize_model(spec).model_json_schema()
    assert a == b


@pytest.mark.unit
def test_invoice_prd_fixture_round_trip() -> None:
    Model = synthesize_model(_load(_invoice_spec()))
    instance = Model(
        vendor_name="Acme",
        invoice_number="INV-001",
        issue_date="2026-03-05",
        total_amount="100.00",
        currency="USD",
    )
    assert instance.vendor_name == "Acme"  # type: ignore[attr-defined]
    assert instance.invoice_number == "INV-001"  # type: ignore[attr-defined]
    assert instance.issue_date == _dt.date(2026, 3, 5)  # type: ignore[attr-defined]
    assert instance.total_amount == Decimal("100.00")  # type: ignore[attr-defined]
    assert instance.currency == "USD"  # type: ignore[attr-defined]
