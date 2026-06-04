"""Unit tests for the ``eval-report.json`` emitter (P4).

Covers the pure serializer in ``prompiler.eval.report_json``: schema shape,
per-field/per-case mapping, ``usage`` mapping (present and ``None``),
deterministic timestamp formatting, ``spec_hash`` verbatim pass-through, and the
``write_report`` round-trip.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest

from prompiler.eval import build_report, dump_report, write_report
from prompiler.eval.metrics import aggregate
from prompiler.eval.report_json import build_report as build_report_direct
from prompiler.eval.runner import CaseResult, EvalResult, EvalUsage, FieldDiff

_TS = datetime(2026, 5, 21, 1, 38, 0, tzinfo=UTC)

_META = {
    "spec": "demo",
    "spec_hash": "abc123",
    "backend": "ollama",
    "model": "qwen2.5:7b",
    "timestamp": _TS,
    "fixture_path": "tests/fixtures/demo.yaml",
}


def _result() -> EvalResult:
    cases = (
        CaseResult(
            name="case-ok",
            diffs=(FieldDiff(field="name", expected="Ada", predicted="Ada", status="match"),),
        ),
        CaseResult(
            name="case-bad",
            diffs=(
                FieldDiff(field="name", expected="Bob", predicted="Bo", status="mismatch"),
                FieldDiff(field="age", expected="30", predicted=None, status="missing"),
            ),
        ),
    )
    observations = [(d.field, d.status) for case in cases for d in case.diffs]
    return EvalResult(cases=cases, metrics=aggregate(observations))


def test_reexported_from_package() -> None:
    assert build_report is build_report_direct


def test_top_level_schema_shape() -> None:
    report = build_report(_result(), **_META)
    assert set(report) == {
        "spec",
        "spec_hash",
        "backend",
        "model",
        "timestamp",
        "fixture_path",
        "aggregate",
        "per_field",
        "per_case",
        "usage",
    }


def test_metadata_passed_through_verbatim() -> None:
    report = build_report(_result(), **_META)
    assert report["spec"] == "demo"
    assert report["spec_hash"] == "abc123"
    assert report["backend"] == "ollama"
    assert report["model"] == "qwen2.5:7b"
    assert report["fixture_path"] == "tests/fixtures/demo.yaml"


def test_spec_hash_not_prefixed() -> None:
    report = build_report(_result(), **{**_META, "spec_hash": "deadbeef"})
    assert report["spec_hash"] == "deadbeef"
    assert not report["spec_hash"].startswith("sha256:")


def test_aggregate_mirrors_metrics() -> None:
    result = _result()
    report = build_report(result, **_META)
    assert report["aggregate"] == {
        "precision": result.metrics.precision,
        "recall": result.metrics.recall,
        "f1": result.metrics.f1,
    }


def test_per_field_uses_short_keys() -> None:
    result = _result()
    report = build_report(result, **_META)
    assert set(report["per_field"]) == set(result.metrics.per_field)
    name_score = result.metrics.per_field["name"]
    assert report["per_field"]["name"] == {
        "p": name_score.precision,
        "r": name_score.recall,
        "f1": name_score.f1,
    }


def test_per_case_serialization() -> None:
    report = build_report(_result(), **_META)
    by_name = {c["name"]: c for c in report["per_case"]}
    assert by_name["case-ok"]["ok"] is True
    assert by_name["case-bad"]["ok"] is False
    assert by_name["case-ok"]["diff"] == [
        {"field": "name", "expected": "Ada", "predicted": "Ada", "status": "match"},
    ]
    assert by_name["case-bad"]["diff"][1] == {
        "field": "age",
        "expected": "30",
        "predicted": None,
        "status": "missing",
    }


def test_per_case_ok_false_when_error() -> None:
    cases = (CaseResult(name="boom", diffs=(), error="timeout"),)
    result = EvalResult(cases=cases, metrics=aggregate([]))
    report = build_report(result, **_META)
    assert report["per_case"][0]["ok"] is False


def test_per_case_ok_true_with_no_diffs_and_no_error() -> None:
    cases = (CaseResult(name="empty", diffs=()),)
    result = EvalResult(cases=cases, metrics=aggregate([]))
    report = build_report(result, **_META)
    assert report["per_case"][0]["ok"] is True


def test_usage_none_serializes_to_null() -> None:
    report = build_report(_result(), **_META)
    assert report["usage"] is None
    assert '"usage": null' in dump_report(report)


def test_usage_present_field_mapping() -> None:
    usage = EvalUsage(calls=3, prompt_tokens=120, completion_tokens=45, cost_usd=0.0012)
    result = EvalResult(cases=(), metrics=aggregate([]), usage=usage)
    report = build_report(result, **_META)
    assert report["usage"] == {
        "input_tokens": 120,
        "output_tokens": 45,
        "cost_estimate_usd": 0.0012,
    }
    assert "calls" not in report["usage"]


def test_timestamp_formatted_utc_z() -> None:
    report = build_report(_result(), **_META)
    assert report["timestamp"] == "2026-05-21T01:38:00Z"


def test_timestamp_naive_treated_as_utc() -> None:
    naive = datetime(2026, 5, 21, 1, 38, 0)
    report = build_report(_result(), **{**_META, "timestamp": naive})
    assert report["timestamp"] == "2026-05-21T01:38:00Z"


def test_timestamp_converted_from_other_zone() -> None:
    from datetime import timedelta

    plus_two = timezone(timedelta(hours=2))
    aware = datetime(2026, 5, 21, 3, 38, 0, tzinfo=plus_two)
    report = build_report(_result(), **{**_META, "timestamp": aware})
    assert report["timestamp"] == "2026-05-21T01:38:00Z"


def test_timestamp_defaults_to_now() -> None:
    meta = {k: v for k, v in _META.items() if k != "timestamp"}
    report = build_report(_result(), **meta)
    assert report["timestamp"].endswith("Z")
    datetime.strptime(report["timestamp"], "%Y-%m-%dT%H:%M:%SZ")


def test_dump_report_is_valid_json_and_preserves_order() -> None:
    report = build_report(_result(), **_META)
    text = dump_report(report)
    assert json.loads(text) == report
    assert list(json.loads(text)) == list(report)


def test_write_report_round_trip_with_trailing_newline(tmp_path: Path) -> None:
    report = build_report(_result(), **_META)
    out = tmp_path / "eval-report.json"
    write_report(report, out)
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == report


def test_write_report_accepts_str_path(tmp_path: Path) -> None:
    report = build_report(_result(), **_META)
    out = tmp_path / "r.json"
    write_report(report, str(out))
    assert json.loads(out.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("indent", [0, 2, 4])
def test_dump_report_indent_param(indent: int) -> None:
    report = build_report(_result(), **_META)
    assert json.loads(dump_report(report, indent=indent)) == report
