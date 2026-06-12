"""Unit tests for prompiler.spec.loader — YAML loader + Pydantic + line/col mapping.

Covers PRD §5 (spec ingestion) + P1.2 task spec:
  - happy paths: invoice + email_category YAML roundtrip via tmp_path
  - str path accepted alongside Path
  - missing file -> SpecLoadError with .file set, "not found" in message
  - YAML syntax error -> .line / .column populated (>=1 / >=0)
  - Pydantic validation error (task: bogus) -> .line maps to YAML node line
  - unknown top-level key rejected
  - extract task without fields rejected
  - empty file rejected
  - non-mapping scalar / sequence root rejected
  - str(SpecLoadError) renders "file:line:col message" form
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from prompiler.spec import EntitySpec, SpecLoadError, load_spec

# ---------------------------------------------------------------------------
# Fixtures (synthetic; mirror PRD §5 examples)
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


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_spec_invoice_roundtrip(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "invoice.yaml", _invoice_spec())
    spec = load_spec(path)
    assert isinstance(spec, EntitySpec)
    assert spec.name == "invoice"
    assert spec.task == "extract"
    assert spec.fields is not None
    assert len(spec.fields) == 5


@pytest.mark.unit
def test_load_spec_email_category_roundtrip(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "email_category.yaml", _email_category_spec())
    spec = load_spec(path)
    assert spec.name == "email_category"
    assert spec.task == "classify"
    assert spec.labels is not None
    assert len(spec.labels) == 4


@pytest.mark.unit
def test_load_spec_accepts_str_path(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "invoice.yaml", _invoice_spec())
    spec = load_spec(str(path))
    assert spec.name == "invoice"


# ---------------------------------------------------------------------------
# IO + parse errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_spec_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(missing)
    err = excinfo.value
    assert err.file == missing
    assert "not found" in str(err).lower()


@pytest.mark.unit
def test_load_spec_yaml_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: invoice\n  bad indent: oops\n: missing key\n", encoding="utf-8")
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    err = excinfo.value
    assert err.file == path
    assert err.line is not None
    assert err.line >= 1
    assert err.column is not None
    assert err.column >= 0


@pytest.mark.unit
def test_load_spec_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    assert excinfo.value.file == path


@pytest.mark.unit
def test_load_spec_scalar_root_rejected(tmp_path: Path) -> None:
    path = tmp_path / "scalar.yaml"
    path.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    assert excinfo.value.file == path


@pytest.mark.unit
def test_load_spec_sequence_root_rejected(tmp_path: Path) -> None:
    path = tmp_path / "seq.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    assert excinfo.value.file == path


# ---------------------------------------------------------------------------
# Validation errors -> YAML line mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_spec_pydantic_error_maps_to_line(tmp_path: Path) -> None:
    data = _invoice_spec()
    data["task"] = "bogus"
    path = _write_yaml(tmp_path, "bad_task.yaml", data)
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    err = excinfo.value
    assert err.file == path
    assert err.line is not None
    assert err.line >= 1


# ---------------------------------------------------------------------------
# spec_version handling (E1: version 2 is the only supported version)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_spec_version_2_succeeds(tmp_path: Path) -> None:
    data = _invoice_spec()
    data["spec_version"] = 2
    path = _write_yaml(tmp_path, "v2.yaml", data)
    spec = load_spec(path)
    assert spec.spec_version == 2


@pytest.mark.unit
def test_load_spec_version_1_rejected_points_to_migrate_spec(tmp_path: Path) -> None:
    data = _invoice_spec()
    data["spec_version"] = 1
    path = _write_yaml(tmp_path, "v1.yaml", data)
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    err = excinfo.value
    assert err.file == path
    assert "migrate-spec" in str(err)
    assert err.line is not None
    assert err.line >= 1


@pytest.mark.unit
def test_load_spec_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    data = _invoice_spec()
    data["bogus"] = "x"
    path = _write_yaml(tmp_path, "unknown_key.yaml", data)
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    assert excinfo.value.file == path


@pytest.mark.unit
def test_load_spec_extract_without_fields_rejected(tmp_path: Path) -> None:
    data = _invoice_spec()
    data.pop("fields")
    path = _write_yaml(tmp_path, "no_fields.yaml", data)
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(path)
    assert excinfo.value.file == path


# ---------------------------------------------------------------------------
# Error rendering
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_load_error_str_includes_location(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(SpecLoadError) as excinfo:
        load_spec(missing)
    rendered = str(excinfo.value)
    assert str(missing) in rendered
    assert ":" in rendered
