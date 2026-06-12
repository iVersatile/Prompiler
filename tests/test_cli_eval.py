"""CLI integration tests for the ``prompiler eval`` subcommand (P4 L188)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from prompiler.cli import main
from prompiler.runtime import registry as _registry


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Reset the shared global registry so each test re-registers cleanly.

    The CLI registers against ``_DEFAULT_REGISTRY`` (no injection point), and
    every test reuses spec name ``invoice`` — without this the second
    registration raises ``ValueError: name 'invoice' is already registered``.
    """
    _registry._DEFAULT_REGISTRY._bundles.clear()


def _clean_extract_spec() -> dict[str, Any]:
    return {
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
            },
            {
                "name": "total_amount",
                "type": "string",
                "required": True,
                "description": "Grand total on the invoice in vendor currency.",
            },
        ],
    }


def _matching_fixture() -> list[dict[str, Any]]:
    return [
        {
            "name": "case-one",
            "input": "Invoice from Acme totalling 100.",
            "expected": {"vendor_name": "mock", "total_amount": "mock"},
        },
        {
            "name": "case-two",
            "input": "Invoice from Globex totalling 200.",
            "expected": {"vendor_name": "mock", "total_amount": "mock"},
        },
    ]


def _write_yaml(path: Path, payload: object) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def spec_path(tmp_path: Path) -> Path:
    return _write_yaml(tmp_path / "spec.yaml", _clean_extract_spec())


@pytest.fixture
def fixtures_path(tmp_path: Path) -> Path:
    return _write_yaml(tmp_path / "fixtures.yaml", _matching_fixture())


@pytest.mark.integration
def test_eval_mock_happy_path(
    spec_path: Path, fixtures_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["eval", str(spec_path), str(fixtures_path), "--backend", "mock"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cases=2" in out
    assert "precision=1.000" in out
    assert "recall=1.000" in out
    assert "f1=1.000" in out


@pytest.mark.unit
def test_eval_missing_spec(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixtures = _write_yaml(tmp_path / "fixtures.yaml", _matching_fixture())
    rc = main(["eval", str(tmp_path / "absent.yaml"), str(fixtures), "--backend", "mock"])
    assert rc == 2
    assert "spec not found" in capsys.readouterr().err


@pytest.mark.unit
def test_eval_missing_fixtures(spec_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["eval", str(spec_path), str(spec_path.parent / "absent.yaml"), "--backend", "mock"])
    assert rc == 2
    assert "fixtures not found" in capsys.readouterr().err


@pytest.mark.unit
def test_eval_spec_load_error(
    tmp_path: Path, fixtures_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _clean_extract_spec()
    bad["task"] = "bogus"
    spec = _write_yaml(tmp_path / "bad_spec.yaml", bad)
    rc = main(["eval", str(spec), str(fixtures_path), "--backend", "mock"])
    assert rc == 1
    assert capsys.readouterr().err.strip()


@pytest.mark.unit
def test_eval_fixture_load_error(
    spec_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_fixtures = tmp_path / "bad_fixtures.yaml"
    bad_fixtures.write_text("name: not-a-list\n", encoding="utf-8")
    rc = main(["eval", str(spec_path), str(bad_fixtures), "--backend", "mock"])
    assert rc == 1
    assert capsys.readouterr().err.strip()


@pytest.mark.integration
def test_eval_writes_json_report(
    spec_path: Path, fixtures_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    json_out = tmp_path / "eval-report.json"
    rc = main(
        [
            "eval",
            str(spec_path),
            str(fixtures_path),
            "--backend",
            "mock",
            "--json-out",
            str(json_out),
        ]
    )
    assert rc == 0
    assert str(json_out) in capsys.readouterr().out
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["spec"] == "invoice"
    assert "per_field" in report
    assert "per_case" in report
    assert "aggregate" in report


@pytest.mark.integration
def test_eval_writes_html_report(
    spec_path: Path, fixtures_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    html_out = tmp_path / "eval-report.html"
    rc = main(
        [
            "eval",
            str(spec_path),
            str(fixtures_path),
            "--backend",
            "mock",
            "--html-out",
            str(html_out),
        ]
    )
    assert rc == 0
    assert str(html_out) in capsys.readouterr().out
    assert html_out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


@pytest.mark.integration
def test_eval_expect_hash_mismatch_warns(
    spec_path: Path, fixtures_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rc = main(
        [
            "eval",
            str(spec_path),
            str(fixtures_path),
            "--backend",
            "mock",
            "--expect-hash",
            "deadbeef",
        ]
    )
    assert rc == 0
    assert any(
        "spec_hash mismatch" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records
    )
