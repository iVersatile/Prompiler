"""Unit tests for prompiler.spec.linter — semantic validator over EntitySpec (P1.4).

Covers PLAN.md L108 scope (verbatim):
  "prompiler validate catches: duplicate field names, unsupported types,
   missing descriptions, reserved names."

Unsupported-type rejection is already enforced at load time by the Pydantic
``FieldType = Literal[...]`` annotation, so the linter focuses on the other
three categories:

  - duplicate-field-name   (sibling-scoped; nested objects evaluated separately)
  - duplicate-label-name
  - missing-description    (entity, each field with a name, each label, recursive)
  - reserved-name          (Python keywords + small Pydantic-reserved attr set)

Linter API under test:

    @dataclass(frozen=True)
    class LintIssue:
        path: str
        code: str
        message: str

    def lint_spec(spec: EntitySpec) -> list[LintIssue]: ...

``lint_spec`` returns a list (possibly empty); it never raises for semantic
issues. A clean spec lints to ``[]``.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompiler.spec import EntitySpec, LintIssue, lint_spec

# ---------------------------------------------------------------------------
# Synthetic, lint-clean fixtures (PRD §5 shape; every field/label carries a
# description so the happy-path tests return zero issues).
# ---------------------------------------------------------------------------


def _clean_invoice_spec() -> dict[str, Any]:
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


def _clean_email_category_spec() -> dict[str, Any]:
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
        "allow_multi_label": False,
    }


def _load(data: dict[str, Any]) -> EntitySpec:
    return EntitySpec.model_validate(data)


def _codes(issues: list[LintIssue]) -> set[str]:
    return {i.code for i in issues}


# ---------------------------------------------------------------------------
# LintIssue shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lint_issue_has_path_code_message() -> None:
    issue = LintIssue(path="fields[0]", code="missing-description", message="x")
    assert issue.path == "fields[0]"
    assert issue.code == "missing-description"
    assert issue.message == "x"


@pytest.mark.unit
def test_lint_issue_is_immutable() -> None:
    issue = LintIssue(path="p", code="c", message="m")
    with pytest.raises((AttributeError, TypeError)):
        issue.code = "other"  # type: ignore[misc]


@pytest.mark.unit
def test_lint_spec_returns_list_not_iterator() -> None:
    issues = lint_spec(_load(_clean_invoice_spec()))
    assert isinstance(issues, list)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clean_invoice_lints_empty() -> None:
    assert lint_spec(_load(_clean_invoice_spec())) == []


@pytest.mark.unit
def test_clean_email_category_lints_empty() -> None:
    assert lint_spec(_load(_clean_email_category_spec())) == []


# ---------------------------------------------------------------------------
# Duplicate names
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_duplicate_top_level_field_names_flagged() -> None:
    data = _clean_invoice_spec()
    data["fields"].append(
        {
            "name": "vendor_name",
            "type": "string",
            "required": False,
            "description": "Second copy of vendor_name to trigger the duplicate check.",
        }
    )
    issues = lint_spec(_load(data))
    assert "duplicate-field-name" in _codes(issues)


@pytest.mark.unit
def test_duplicate_field_names_inside_object_flagged() -> None:
    data = _clean_invoice_spec()
    data["fields"].append(
        {
            "name": "billing_address",
            "type": "object",
            "required": False,
            "description": "Billing address block.",
            "fields": [
                {
                    "name": "city",
                    "type": "string",
                    "required": True,
                    "description": "City line of the billing address.",
                },
                {
                    "name": "city",
                    "type": "string",
                    "required": False,
                    "description": "Duplicate city sibling — should be flagged.",
                },
            ],
        }
    )
    issues = lint_spec(_load(data))
    assert "duplicate-field-name" in _codes(issues)


@pytest.mark.unit
def test_distinct_names_across_unrelated_objects_not_flagged() -> None:
    data = _clean_invoice_spec()
    address_children = [
        {
            "name": "city",
            "type": "string",
            "required": True,
            "description": "City line.",
        },
        {
            "name": "zip",
            "type": "string",
            "required": True,
            "description": "Postal code.",
        },
    ]
    data["fields"].append(
        {
            "name": "billing_address",
            "type": "object",
            "required": False,
            "description": "Billing address block.",
            "fields": address_children,
        }
    )
    data["fields"].append(
        {
            "name": "shipping_address",
            "type": "object",
            "required": False,
            "description": "Shipping address block.",
            "fields": address_children,
        }
    )
    issues = lint_spec(_load(data))
    assert "duplicate-field-name" not in _codes(issues)


@pytest.mark.unit
def test_duplicate_label_names_flagged() -> None:
    data = _clean_email_category_spec()
    data["labels"].append({"name": "billing", "description": "Duplicate billing label."})
    issues = lint_spec(_load(data))
    assert "duplicate-label-name" in _codes(issues)


# ---------------------------------------------------------------------------
# Missing descriptions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_top_level_description_flagged() -> None:
    data = _clean_invoice_spec()
    data.pop("description")
    issues = lint_spec(_load(data))
    assert "missing-description" in _codes(issues)


@pytest.mark.unit
def test_missing_field_description_flagged() -> None:
    data = _clean_invoice_spec()
    del data["fields"][2]["description"]  # issue_date
    issues = lint_spec(_load(data))
    assert "missing-description" in _codes(issues)


@pytest.mark.unit
def test_missing_label_description_flagged() -> None:
    data = _clean_email_category_spec()
    del data["labels"][0]["description"]  # billing
    issues = lint_spec(_load(data))
    assert "missing-description" in _codes(issues)


@pytest.mark.unit
def test_missing_nested_field_description_flagged() -> None:
    data = _clean_invoice_spec()
    data["fields"].append(
        {
            "name": "billing_address",
            "type": "object",
            "required": False,
            "description": "Billing address block.",
            "fields": [
                {
                    "name": "city",
                    "type": "string",
                    "required": True,
                    # description omitted — should be flagged
                },
            ],
        }
    )
    issues = lint_spec(_load(data))
    assert "missing-description" in _codes(issues)


# ---------------------------------------------------------------------------
# Reserved names
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("reserved", ["class", "return", "def", "import"])
def test_reserved_python_keyword_as_field_name_flagged(reserved: str) -> None:
    data = _clean_invoice_spec()
    data["fields"].append(
        {
            "name": reserved,
            "type": "string",
            "required": False,
            "description": f"Field named after Python keyword {reserved!r}.",
        }
    )
    issues = lint_spec(_load(data))
    assert "reserved-name" in _codes(issues)


@pytest.mark.unit
def test_reserved_python_keyword_as_label_name_flagged() -> None:
    data = _clean_email_category_spec()
    data["labels"].append({"name": "class", "description": "Label named after Python keyword."})
    issues = lint_spec(_load(data))
    assert "reserved-name" in _codes(issues)


@pytest.mark.unit
def test_reserved_pydantic_attr_as_field_name_flagged() -> None:
    data = _clean_invoice_spec()
    data["fields"].append(
        {
            "name": "model_config",
            "type": "string",
            "required": False,
            "description": "Field colliding with Pydantic reserved attribute.",
        }
    )
    issues = lint_spec(_load(data))
    assert "reserved-name" in _codes(issues)


@pytest.mark.unit
def test_reserved_check_skips_unnamed_array_item() -> None:
    data = _clean_invoice_spec()
    data["fields"].append(
        {
            "name": "tags",
            "type": "array",
            "required": False,
            "description": "Free-form tags applied to the invoice.",
            "item": {
                "type": "string",
                # no name — array element is positional
            },
        }
    )
    # Must not raise; array-element item with no name must not be evaluated
    # against reserved-name / duplicate-name rules.
    issues = lint_spec(_load(data))
    assert "reserved-name" not in _codes(issues)


# ---------------------------------------------------------------------------
# Multi-issue aggregation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_multiple_issue_categories_returned_together() -> None:
    data = _clean_invoice_spec()
    del data["fields"][2]["description"]  # missing-description
    data["fields"].append(
        {
            "name": "vendor_name",  # duplicate-field-name
            "type": "string",
            "required": False,
            "description": "Duplicate vendor_name.",
        }
    )
    data["fields"].append(
        {
            "name": "class",  # reserved-name
            "type": "string",
            "required": False,
            "description": "Reserved keyword field.",
        }
    )
    codes = _codes(lint_spec(_load(data)))
    assert {"missing-description", "duplicate-field-name", "reserved-name"} <= codes
