"""Unit tests for prompiler.eval.fixtures — YAML fixture loader (P4).

Covers PRD FR-6 (fixture format) + P4 task 1 spec:
  - happy path: multi-case fixture roundtrip via tmp_path
  - str path accepted alongside Path
  - missing file -> EvalError, "not found" in message
  - YAML syntax error -> file:line:col rendered in message
  - empty file rejected
  - non-sequence root rejected (mapping / scalar)
  - non-mapping case rejected
  - missing / wrong-type name rejected
  - missing / wrong-type input rejected
  - missing / wrong-type expected rejected
  - duplicate case name rejected
  - expected exposed as read-only mapping (mutation raises)
  - load_fixtures returns a tuple
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prompiler.eval import FixtureCase, load_fixtures
from prompiler.runtime.errors import EvalError

# ---------------------------------------------------------------------------
# Fixtures (synthetic; mirror PRD FR-6 examples)
# ---------------------------------------------------------------------------


_GOOD_FIXTURE = """\
- name: simple_acme_invoice
  input: |
    ACME Corp invoice, total $1,234.56 USD.
  expected:
    vendor_name: ACME Corp
    total_amount: "1234.56"
    currency: USD
- name: globex_invoice
  input: |
    Globex Inc, total 999.00 EUR.
  expected:
    vendor_name: Globex Inc
    currency: EUR
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_fixtures_roundtrip(tmp_path: Path) -> None:
    path = _write(tmp_path, "fixture.yaml", _GOOD_FIXTURE)
    cases = load_fixtures(path)
    assert isinstance(cases, tuple)
    assert len(cases) == 2
    assert all(isinstance(c, FixtureCase) for c in cases)
    first = cases[0]
    assert first.name == "simple_acme_invoice"
    assert "ACME Corp" in first.input
    assert first.expected["currency"] == "USD"
    assert first.expected["total_amount"] == "1234.56"


@pytest.mark.unit
def test_load_fixtures_accepts_str_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "fixture.yaml", _GOOD_FIXTURE)
    cases = load_fixtures(str(path))
    assert cases[0].name == "simple_acme_invoice"


@pytest.mark.unit
def test_expected_is_read_only(tmp_path: Path) -> None:
    path = _write(tmp_path, "fixture.yaml", _GOOD_FIXTURE)
    cases = load_fixtures(path)
    with pytest.raises(TypeError):
        cases[0].expected["currency"] = "GBP"  # type: ignore[index]


# ---------------------------------------------------------------------------
# IO + parse errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(missing)
    rendered = str(excinfo.value)
    assert str(missing) in rendered
    assert "not found" in rendered.lower()


@pytest.mark.unit
def test_yaml_syntax_error_renders_location(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.yaml", "- name: x\n  bad indent: oops\n: missing key\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    rendered = str(excinfo.value)
    assert str(path) in rendered
    assert ":" in rendered


@pytest.mark.unit
def test_empty_file_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.yaml", "")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "empty" in str(excinfo.value).lower()


@pytest.mark.unit
def test_mapping_root_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "map.yaml", "name: x\ninput: y\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "sequence" in str(excinfo.value).lower()


@pytest.mark.unit
def test_scalar_root_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "scalar.yaml", "just a string\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "sequence" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Case-shape errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_mapping_case_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- not a mapping\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "mapping" in str(excinfo.value).lower()


@pytest.mark.unit
def test_missing_name_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- input: hi\n  expected: {}\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "name" in str(excinfo.value).lower()


@pytest.mark.unit
def test_blank_name_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", '- name: "   "\n  input: hi\n  expected: {}\n')
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "name" in str(excinfo.value).lower()


@pytest.mark.unit
def test_non_string_name_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- name: 42\n  input: hi\n  expected: {}\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "name" in str(excinfo.value).lower()


@pytest.mark.unit
def test_missing_input_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- name: x\n  expected: {}\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "input" in str(excinfo.value).lower()


@pytest.mark.unit
def test_non_string_input_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- name: x\n  input: 123\n  expected: {}\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "input" in str(excinfo.value).lower()


@pytest.mark.unit
def test_missing_expected_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- name: x\n  input: hi\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "expected" in str(excinfo.value).lower()


@pytest.mark.unit
def test_non_mapping_expected_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", "- name: x\n  input: hi\n  expected: not_a_map\n")
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "expected" in str(excinfo.value).lower()


@pytest.mark.unit
def test_duplicate_name_rejected(tmp_path: Path) -> None:
    text = "- name: dup\n  input: a\n  expected: {}\n- name: dup\n  input: b\n  expected: {}\n"
    path = _write(tmp_path, "f.yaml", text)
    with pytest.raises(EvalError) as excinfo:
        load_fixtures(path)
    assert "duplicate" in str(excinfo.value).lower()
