"""Unit tests for prompiler.compiler.constraints — cross-field constraint compiler (P1.6).

Covers PLAN.md L97 scope (verbatim):
  "Cross-field constraint compiler (validators)."

Each `cross_field_constraints[*].expr` becomes a runtime model validator on the
synthesised Pydantic model. Evaluation uses an AST whitelist (no `eval`), supports
dot-path projection across array-of-object fields (e.g. ``line_items.line_total``
=> ``[item.line_total for item in line_items]``), and dispatches by severity:

  - severity=error => ValidationError on violation
  - severity=warn  => ``warnings.warn`` on violation, model still validates

Compile-time errors (raised inside ``synthesize_model``):
  - unknown top-level field references
  - syntax errors in the expression
  - non-whitelisted function calls (only sum/len/min/max/abs allowed)
  - non-whitelisted AST nodes (lambdas, comprehensions, attribute dunders, …)
"""

from __future__ import annotations

import warnings
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from prompiler.compiler import synthesize_model
from prompiler.spec import EntitySpec


def _load(data: dict[str, Any]) -> EntitySpec:
    return EntitySpec.model_validate(data)


def _invoice_with_constraint(expr: str, severity: str = "error") -> EntitySpec:
    return _load(
        {
            "spec_version": 2,
            "name": "invoice",
            "task": "extract",
            "description": "Invoice with cross-field constraint.",
            "fields": [
                {
                    "name": "total_amount",
                    "type": "decimal",
                    "required": True,
                    "description": "total due",
                },
                {
                    "name": "line_items",
                    "type": "array",
                    "description": "line items",
                    "item": {
                        "type": "object",
                        "fields": [
                            {
                                "name": "line_total",
                                "type": "decimal",
                                "description": "per-line total",
                            },
                        ],
                    },
                },
            ],
            "cross_field_constraints": [{"expr": expr, "severity": severity}],
        }
    )


def _scalar_pair_spec(expr: str, severity: str = "error") -> EntitySpec:
    return _load(
        {
            "spec_version": 2,
            "name": "pair",
            "task": "extract",
            "description": "Two-number spec.",
            "fields": [
                {"name": "a", "type": "integer", "description": "first"},
                {"name": "b", "type": "integer", "description": "second"},
            ],
            "cross_field_constraints": [{"expr": expr, "severity": severity}],
        }
    )


def _triple_spec(expr: str, severity: str = "error") -> EntitySpec:
    return _load(
        {
            "spec_version": 2,
            "name": "triple",
            "task": "extract",
            "description": "Three-number spec.",
            "fields": [
                {"name": "a", "type": "integer", "description": "first"},
                {"name": "b", "type": "integer", "description": "second"},
                {"name": "c", "type": "integer", "description": "third"},
            ],
            "cross_field_constraints": [{"expr": expr, "severity": severity}],
        }
    )


@pytest.mark.unit
def test_spec_without_constraints_validates_normally() -> None:
    spec = _load(
        {
            "spec_version": 2,
            "name": "pair",
            "task": "extract",
            "description": "Two-number spec, no constraints.",
            "fields": [
                {"name": "a", "type": "integer", "description": "first"},
                {"name": "b", "type": "integer", "description": "second"},
            ],
        }
    )
    model = synthesize_model(spec)
    instance = model(a=1, b=2)
    assert instance.a == 1  # type: ignore[attr-defined]
    assert instance.b == 2  # type: ignore[attr-defined]


@pytest.mark.unit
def test_empty_constraints_list_does_not_alter_validation() -> None:
    spec = _scalar_pair_spec("a == b")
    spec_no_cc = spec.model_copy(update={"cross_field_constraints": []})
    model = synthesize_model(spec_no_cc)
    model(a=1, b=2)


@pytest.mark.unit
def test_equality_pass() -> None:
    model = synthesize_model(_scalar_pair_spec("a == b"))
    model(a=5, b=5)


@pytest.mark.unit
def test_equality_fail_raises_when_error_severity() -> None:
    model = synthesize_model(_scalar_pair_spec("a == b", severity="error"))
    with pytest.raises(ValidationError):
        model(a=1, b=2)


@pytest.mark.unit
def test_less_than() -> None:
    model = synthesize_model(_scalar_pair_spec("a < b"))
    model(a=1, b=2)
    with pytest.raises(ValidationError):
        model(a=2, b=1)


@pytest.mark.unit
def test_greater_than_or_equal() -> None:
    model = synthesize_model(_scalar_pair_spec("a >= b"))
    model(a=2, b=2)
    with pytest.raises(ValidationError):
        model(a=1, b=2)


@pytest.mark.unit
def test_not_equal() -> None:
    model = synthesize_model(_scalar_pair_spec("a != b"))
    model(a=1, b=2)
    with pytest.raises(ValidationError):
        model(a=2, b=2)


@pytest.mark.unit
def test_arithmetic_addition() -> None:
    model = synthesize_model(_triple_spec("a + b == c"))
    model(a=2, b=3, c=5)
    with pytest.raises(ValidationError):
        model(a=2, b=3, c=6)


@pytest.mark.unit
def test_arithmetic_subtraction_and_multiplication() -> None:
    model = synthesize_model(_scalar_pair_spec("a * 2 - b == 0"))
    model(a=3, b=6)
    with pytest.raises(ValidationError):
        model(a=3, b=7)


@pytest.mark.unit
def test_boolean_and() -> None:
    model = synthesize_model(_triple_spec("a < b and b < c"))
    model(a=1, b=2, c=3)
    with pytest.raises(ValidationError):
        model(a=1, b=3, c=2)


@pytest.mark.unit
def test_boolean_or() -> None:
    model = synthesize_model(_scalar_pair_spec("a == 0 or b == 0"))
    model(a=0, b=5)
    model(a=5, b=0)
    with pytest.raises(ValidationError):
        model(a=1, b=1)


@pytest.mark.unit
def test_unary_not() -> None:
    model = synthesize_model(_scalar_pair_spec("not (a == b)"))
    model(a=1, b=2)
    with pytest.raises(ValidationError):
        model(a=1, b=1)


@pytest.mark.unit
def test_sum_over_array_projection_equals_scalar() -> None:
    spec = _invoice_with_constraint("sum(line_items.line_total) == total_amount", severity="error")
    model = synthesize_model(spec)
    model(
        total_amount=Decimal("30.00"),
        line_items=[{"line_total": Decimal("10.00")}, {"line_total": Decimal("20.00")}],
    )
    with pytest.raises(ValidationError):
        model(
            total_amount=Decimal("30.00"),
            line_items=[
                {"line_total": Decimal("10.00")},
                {"line_total": Decimal("19.00")},
            ],
        )


@pytest.mark.unit
def test_len_of_array_projection() -> None:
    spec = _load(
        {
            "spec_version": 2,
            "name": "tagged",
            "task": "extract",
            "description": "Spec with array of objects.",
            "fields": [
                {
                    "name": "expected",
                    "type": "integer",
                    "description": "expected count",
                },
                {
                    "name": "items",
                    "type": "array",
                    "description": "items",
                    "item": {
                        "type": "object",
                        "fields": [
                            {"name": "name", "type": "string", "description": "name"},
                        ],
                    },
                },
            ],
            "cross_field_constraints": [{"expr": "len(items) == expected", "severity": "error"}],
        }
    )
    model = synthesize_model(spec)
    model(expected=2, items=[{"name": "x"}, {"name": "y"}])
    with pytest.raises(ValidationError):
        model(expected=3, items=[{"name": "x"}, {"name": "y"}])


@pytest.mark.unit
def test_min_and_max_calls() -> None:
    model_min = synthesize_model(_triple_spec("min(a, b) == c"))
    model_min(a=1, b=2, c=1)
    with pytest.raises(ValidationError):
        model_min(a=1, b=2, c=2)
    model_max = synthesize_model(_triple_spec("max(a, b) == c"))
    model_max(a=1, b=2, c=2)
    with pytest.raises(ValidationError):
        model_max(a=1, b=2, c=1)


@pytest.mark.unit
def test_abs_call() -> None:
    model = synthesize_model(_scalar_pair_spec("abs(a - b) < 2"))
    model(a=5, b=6)
    model(a=5, b=4)
    with pytest.raises(ValidationError):
        model(a=5, b=10)


@pytest.mark.unit
def test_severity_warn_emits_warning_but_validates() -> None:
    spec = _scalar_pair_spec("a == b", severity="warn")
    model = synthesize_model(spec)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        instance = model(a=1, b=2)
    assert instance.a == 1  # type: ignore[attr-defined]
    assert instance.b == 2  # type: ignore[attr-defined]
    assert any("a == b" in str(w.message) for w in caught)


@pytest.mark.unit
def test_severity_warn_no_warning_on_pass() -> None:
    spec = _scalar_pair_spec("a == b", severity="warn")
    model = synthesize_model(spec)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model(a=3, b=3)
    assert not any("a == b" in str(w.message) for w in caught)


@pytest.mark.unit
def test_severity_error_does_not_emit_warning_on_pass() -> None:
    spec = _scalar_pair_spec("a == b", severity="error")
    model = synthesize_model(spec)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model(a=3, b=3)
    assert not caught


@pytest.mark.unit
def test_multiple_constraints_all_evaluated() -> None:
    spec = _load(
        {
            "spec_version": 2,
            "name": "pair",
            "task": "extract",
            "description": "Pair with two constraints.",
            "fields": [
                {"name": "a", "type": "integer", "description": "first"},
                {"name": "b", "type": "integer", "description": "second"},
            ],
            "cross_field_constraints": [
                {"expr": "a < b", "severity": "error"},
                {"expr": "a + b == 10", "severity": "error"},
            ],
        }
    )
    model = synthesize_model(spec)
    model(a=3, b=7)
    with pytest.raises(ValidationError):
        model(a=4, b=7)
    with pytest.raises(ValidationError):
        model(a=7, b=3)


@pytest.mark.unit
def test_unknown_field_reference_raises_at_compile_time() -> None:
    spec = _scalar_pair_spec("a == c", severity="error")
    with pytest.raises(ValueError, match="unknown field"):
        synthesize_model(spec)


@pytest.mark.unit
def test_invalid_syntax_raises_at_compile_time() -> None:
    spec = _scalar_pair_spec("a == ", severity="error")
    with pytest.raises(ValueError, match="invalid"):
        synthesize_model(spec)


@pytest.mark.unit
def test_disallowed_function_raises_at_compile_time() -> None:
    spec = _scalar_pair_spec("__import__('os').system('echo x') or a == b")
    with pytest.raises(ValueError):
        synthesize_model(spec)


@pytest.mark.unit
def test_attribute_access_to_dunder_raises_at_compile_time() -> None:
    spec = _scalar_pair_spec("a.__class__ == b.__class__")
    with pytest.raises(ValueError):
        synthesize_model(spec)


@pytest.mark.unit
def test_lambda_or_comprehension_raises_at_compile_time() -> None:
    spec = _scalar_pair_spec("(lambda x: x)(a) == b")
    with pytest.raises(ValueError):
        synthesize_model(spec)
    spec_compr = _scalar_pair_spec("[x for x in [a, b]] == [a, b]")
    with pytest.raises(ValueError):
        synthesize_model(spec_compr)


@pytest.mark.unit
def test_decimal_sum_uses_decimal_arithmetic() -> None:
    spec = _invoice_with_constraint("sum(line_items.line_total) == total_amount", severity="error")
    model = synthesize_model(spec)
    model(
        total_amount=Decimal("0.3"),
        line_items=[{"line_total": Decimal("0.1")}, {"line_total": Decimal("0.2")}],
    )


@pytest.mark.unit
def test_same_spec_with_constraints_produces_equivalent_json_schema() -> None:
    spec = _invoice_with_constraint("sum(line_items.line_total) == total_amount", severity="error")
    a = synthesize_model(spec)
    b = synthesize_model(spec)
    assert a.model_json_schema() == b.model_json_schema()
