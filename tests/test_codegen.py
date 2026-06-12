"""Unit tests for prompiler.codegen — static-codegen path (P1.11).

Covers PLAN.md L102:
- ``render(spec) -> str`` produces a parseable, standalone Python module.
- Generated file pins ``COMPILER_PROTOCOL_VERSION = "2"`` and
  ``SPEC_HASH = "<digest>"`` as module-level literals (drift detector).
- Generated file imports only stdlib + ``pydantic`` — no ``prompiler.*``
  runtime dependency (PLAN.md L102: "without a `prompiler` runtime
  dependency").
- Deterministic — same spec compiled twice yields byte-identical source.
- Extract model: type annotations + ``Field(...)`` constraints + optional
  branches + nested object class.
- Classify model: single → ``Literal[...]``; multi → ``list[Literal[...]]``.
- ``write(spec, out_dir) -> Path`` writes ``<out_dir>/<spec.name>.py``,
  creates missing parents, returns the path, content == ``render(spec)``,
  file compiles via ``py_compile``.
"""

from __future__ import annotations

import ast
import py_compile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prompiler import COMPILER_PROTOCOL_VERSION
from prompiler.codegen import render, write
from prompiler.spec import EntitySpec, spec_hash


def _extract_spec(**overrides: Any) -> EntitySpec:
    payload: dict[str, Any] = {
        "spec_version": 2,
        "name": "invoice",
        "task": "extract",
        "description": "Extract billing details from a single invoice document.",
        "fields": [
            {
                "name": "vendor_name",
                "type": "string",
                "required": True,
                "description": "Legal name of the issuing vendor.",
                "pattern": r"^[A-Za-z][A-Za-z0-9 .,'&-]+$",
            },
            {
                "name": "total_amount",
                "type": "decimal",
                "required": True,
                "description": "Grand total on the invoice in vendor currency.",
                "minimum": 0,
            },
            {
                "name": "currency",
                "type": "enum",
                "required": True,
                "description": "ISO 4217 currency code.",
                "values": ["USD", "EUR", "GBP"],
            },
            {
                "name": "memo",
                "type": "string",
                "required": False,
                "description": "Optional free-text memo from the buyer.",
            },
            {
                "name": "line_items",
                "type": "array",
                "required": True,
                "description": "Each billable line on the invoice.",
                "item": {
                    "type": "object",
                    "required": True,
                    "description": "One billable line.",
                    "fields": [
                        {
                            "name": "sku",
                            "type": "string",
                            "required": True,
                            "description": "Vendor SKU.",
                        },
                        {
                            "name": "quantity",
                            "type": "integer",
                            "required": True,
                            "description": "Quantity billed.",
                            "minimum": 1,
                        },
                    ],
                },
            },
        ],
    }
    payload.update(overrides)
    return EntitySpec.model_validate(payload)


def _classify_spec(**overrides: Any) -> EntitySpec:
    payload: dict[str, Any] = {
        "spec_version": 2,
        "name": "email_category",
        "task": "classify",
        "description": "Route inbound support email into one routing bucket.",
        "labels": [
            {"name": "billing", "description": "Payments, invoices, refunds."},
            {"name": "technical", "description": "Product not working."},
            {"name": "sales", "description": "Pre-sales questions."},
        ],
    }
    payload.update(overrides)
    return EntitySpec.model_validate(payload)


def _exec_source(source: str) -> dict[str, Any]:
    ns: dict[str, Any] = {}
    exec(compile(source, "<codegen>", "exec"), ns)
    return ns


@pytest.mark.unit
def test_render_returns_parseable_python() -> None:
    source = render(_extract_spec())
    ast.parse(source)


@pytest.mark.unit
def test_render_pins_protocol_version_literal() -> None:
    source = render(_extract_spec())
    assert f'COMPILER_PROTOCOL_VERSION = "{COMPILER_PROTOCOL_VERSION}"' in source


@pytest.mark.unit
def test_render_pins_spec_hash_literal() -> None:
    spec = _extract_spec()
    source = render(spec)
    digest = spec_hash(spec)
    assert f'SPEC_HASH = "{digest}"' in source


@pytest.mark.unit
def test_render_emits_extract_model_class() -> None:
    source = render(_extract_spec())
    assert "class Invoice(" in source


@pytest.mark.unit
def test_render_emits_classify_model_class() -> None:
    source = render(_classify_spec())
    assert "class EmailCategory(" in source


@pytest.mark.unit
def test_render_no_prompiler_imports() -> None:
    source = render(_extract_spec())
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("prompiler"), (
                    f"generated file must not import prompiler.* (got {alias.name})"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("prompiler"), (
                f"generated file must not import from prompiler.* (got {module})"
            )


@pytest.mark.unit
def test_render_deterministic() -> None:
    spec = _extract_spec()
    assert render(spec) == render(spec)


@pytest.mark.unit
def test_render_extract_model_validates_payload() -> None:
    source = render(_extract_spec())
    ns = _exec_source(source)
    Invoice = ns["Invoice"]
    instance = Invoice.model_validate(
        {
            "vendor_name": "Acme Co",
            "total_amount": "199.99",
            "currency": "USD",
            "line_items": [{"sku": "SKU-1", "quantity": 2}],
        }
    )
    assert instance.vendor_name == "Acme Co"
    assert instance.memo is None


@pytest.mark.unit
def test_render_extract_model_enforces_pattern() -> None:
    source = render(_extract_spec())
    ns = _exec_source(source)
    Invoice = ns["Invoice"]
    with pytest.raises(ValidationError):
        Invoice.model_validate(
            {
                "vendor_name": "  starts with space",
                "total_amount": "10",
                "currency": "USD",
                "line_items": [{"sku": "SKU-1", "quantity": 1}],
            }
        )


@pytest.mark.unit
def test_render_extract_model_enforces_minimum() -> None:
    source = render(_extract_spec())
    ns = _exec_source(source)
    Invoice = ns["Invoice"]
    with pytest.raises(ValidationError):
        Invoice.model_validate(
            {
                "vendor_name": "Acme",
                "total_amount": "-1",
                "currency": "USD",
                "line_items": [{"sku": "SKU-1", "quantity": 1}],
            }
        )


@pytest.mark.unit
def test_render_classify_single_label() -> None:
    source = render(_classify_spec())
    ns = _exec_source(source)
    EmailCategory = ns["EmailCategory"]
    instance = EmailCategory.model_validate({"label": "billing"})
    assert instance.label == "billing"
    with pytest.raises(ValidationError):
        EmailCategory.model_validate({"label": "garbage"})


@pytest.mark.unit
def test_render_classify_multi_label() -> None:
    source = render(_classify_spec(allow_multi_label=True))
    ns = _exec_source(source)
    EmailCategory = ns["EmailCategory"]
    instance = EmailCategory.model_validate({"labels": ["billing", "sales"]})
    assert list(instance.labels) == ["billing", "sales"]
    with pytest.raises(ValidationError):
        EmailCategory.model_validate({"labels": ["nope"]})


@pytest.mark.unit
def test_render_extract_extra_field_forbidden() -> None:
    source = render(_extract_spec())
    ns = _exec_source(source)
    Invoice = ns["Invoice"]
    with pytest.raises(ValidationError):
        Invoice.model_validate(
            {
                "vendor_name": "Acme",
                "total_amount": "10",
                "currency": "USD",
                "line_items": [{"sku": "SKU-1", "quantity": 1}],
                "stowaway": "x",
            }
        )


@pytest.mark.unit
def test_write_creates_named_file(tmp_path: Path) -> None:
    spec = _extract_spec()
    path = write(spec, tmp_path)
    assert path == tmp_path / "invoice.py"
    assert path.read_text(encoding="utf-8") == render(spec)


@pytest.mark.unit
def test_write_creates_missing_parents(tmp_path: Path) -> None:
    spec = _extract_spec()
    nested = tmp_path / "a" / "b" / "c"
    path = write(spec, nested)
    assert path == nested / "invoice.py"
    assert path.exists()


@pytest.mark.unit
def test_write_output_is_py_compileable(tmp_path: Path) -> None:
    spec = _classify_spec()
    path = write(spec, tmp_path)
    py_compile.compile(str(path), doraise=True)
