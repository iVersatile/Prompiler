"""Unit + golden-file snapshot tests for the ``eval-report.html`` renderer (P4).

Covers the pure renderer in ``prompiler.eval.report_html``: self-contained output
(no external references), deterministic markup pinned by a golden-file snapshot,
HTML-escaping of every report value (XSS guard), empty ``per_field``/``per_case``
fallbacks, ``usage`` present vs ``None``, and the ``write_html_report`` round-trip.

Regenerate the golden file after an intentional markup change with::

    PROMPILER_REGEN_GOLDEN=1 uv run pytest tests/test_eval_report_html.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from prompiler.eval import build_html_report, build_report, write_html_report
from prompiler.eval.metrics import aggregate
from prompiler.eval.report_html import build_html_report as build_html_report_direct
from prompiler.eval.runner import CaseResult, EvalResult, EvalUsage, FieldDiff

_GOLDEN = Path(__file__).parent / "fixtures" / "eval_report_golden.html"

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


def _report(**overrides: object) -> dict[str, object]:
    meta = {**_META, **overrides}
    return build_report(_result(), **meta)  # type: ignore[arg-type]


def test_reexported_from_package() -> None:
    assert build_html_report is build_html_report_direct


def test_golden_snapshot_matches() -> None:
    html = build_html_report(_report())
    if os.environ.get("PROMPILER_REGEN_GOLDEN"):
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(html, encoding="utf-8")
    assert _GOLDEN.exists(), "golden missing; run with PROMPILER_REGEN_GOLDEN=1 to create it"
    assert html == _GOLDEN.read_text(encoding="utf-8")


def test_output_is_deterministic() -> None:
    report = _report()
    assert build_html_report(report) == build_html_report(report)


def test_self_contained_no_external_refs() -> None:
    html = build_html_report(_report())
    assert "<style>" in html and "<script>" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "src=" not in html
    assert 'rel="stylesheet"' not in html


def test_structure_landmarks() -> None:
    html = build_html_report(_report())
    assert html.startswith("<!DOCTYPE html>\n")
    assert html.endswith("</html>\n")
    assert '<html lang="en">' in html
    assert html.count("<h1>") == 1
    assert 'name="viewport"' in html
    assert 'scope="col"' in html
    assert 'aria-label="Filter cases"' in html


def test_metadata_rendered() -> None:
    html = build_html_report(_report())
    assert ">demo<" in html
    assert ">abc123<" in html
    assert ">ollama<" in html
    assert ">qwen2.5:7b<" in html
    assert "2026-05-21T01:38:00Z" in html


def test_per_case_status_and_diff() -> None:
    html = build_html_report(_report())
    assert "case-ok" in html
    assert "case-bad" in html
    assert ">pass<" in html
    assert ">fail<" in html
    assert "status-match" in html
    assert "status-mismatch" in html
    assert "status-missing" in html


def test_values_html_escaped() -> None:
    report = _report()
    report["per_case"][0]["diff"][0]["expected"] = '<script>alert("xss")</script>'  # type: ignore[index]
    html = build_html_report(report)
    assert '<script>alert("xss")</script>' not in html
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in html


def test_spec_in_title_escaped() -> None:
    html = build_html_report(_report(spec="<b>x</b>"))
    assert "<title>Eval report — &lt;b&gt;x&lt;/b&gt;</title>" in html
    assert "<b>x</b>" not in html


def test_empty_per_field_fallback() -> None:
    result = EvalResult(cases=(CaseResult(name="empty", diffs=()),), metrics=aggregate([]))
    html = build_html_report(build_report(result, **_META))  # type: ignore[arg-type]
    assert "No fields scored." in html


def test_empty_per_case_fallback() -> None:
    result = EvalResult(cases=(), metrics=aggregate([]))
    html = build_html_report(build_report(result, **_META))  # type: ignore[arg-type]
    assert "No cases." in html


def test_usage_none_message() -> None:
    html = build_html_report(_report())
    assert "Usage metrics were not captured for this run." in html


def test_usage_present_rendered() -> None:
    usage = EvalUsage(calls=3, prompt_tokens=120, completion_tokens=45, cost_usd=0.0012)
    result = EvalResult(cases=(), metrics=aggregate([]), usage=usage)
    html = build_html_report(build_report(result, **_META))  # type: ignore[arg-type]
    assert "Input tokens" in html
    assert ">120<" in html
    assert ">45<" in html
    assert "0.0012" in html
    assert "Usage metrics were not captured" not in html


def test_none_predicted_renders_em_dash() -> None:
    html = build_html_report(_report())
    assert "predicted —" in html


def test_write_html_report_round_trip(tmp_path: Path) -> None:
    report = _report()
    out = tmp_path / "eval-report.html"
    write_html_report(report, out)
    assert out.read_text(encoding="utf-8") == build_html_report(report)


def test_write_html_report_accepts_str_path(tmp_path: Path) -> None:
    report = _report()
    out = tmp_path / "r.html"
    write_html_report(report, str(out))
    assert out.read_text(encoding="utf-8") == build_html_report(report)
