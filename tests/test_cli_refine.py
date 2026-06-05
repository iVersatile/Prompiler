"""CLI tests for ``prompiler refine`` refusal-mode handling (P5, L213).

The refine subcommand surfaces a tutor refusal (or any AdapterError) as a
non-zero process exit — no silent fallback. The happy path emits the proposed
unified diff to stdout. These tests drive that contract via ``main([...])``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompiler.cli import main


def _write_report(path: Path) -> None:
    """Write a minimal eval-report.json the tutor prompt can summarise."""
    payload = {
        "spec": "demo",
        "spec_hash": "deadbeef",
        "backend": "mock",
        "model": "mock",
        "aggregate": {"precision": 0.5, "recall": 0.5, "f1": 0.5},
        "per_field": {"title": {"p": 0.5, "r": 0.5, "f1": 0.5}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def report_path(tmp_path: Path) -> Path:
    path = tmp_path / "eval-report.json"
    _write_report(path)
    return path


@pytest.fixture
def prompt_path(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.txt"
    path.write_text("Extract the title from the document.\n", encoding="utf-8")
    return path


def test_refine_refusal_returns_nonzero(
    report_path: Path, prompt_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # MockAdapter returns {"decline": "mock"} (truthy) -> real refusal path.
    rc = main(["refine", str(report_path), str(prompt_path), "--backend", "mock"])

    assert rc == 1
    assert "declined" in capsys.readouterr().err


def test_refine_missing_report_returns_2(
    tmp_path: Path, prompt_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["refine", str(tmp_path / "nope.json"), str(prompt_path), "--backend", "mock"])

    assert rc == 2
    assert "report not found" in capsys.readouterr().err


def test_refine_missing_prompt_returns_2(
    report_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["refine", str(report_path), str(tmp_path / "nope.txt"), "--backend", "mock"])

    assert rc == 2
    assert "prompt not found" in capsys.readouterr().err


def test_refine_malformed_report_returns_1(
    tmp_path: Path, prompt_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    rc = main(["refine", str(bad), str(prompt_path), "--backend", "mock"])

    assert rc == 1
    assert "invalid report JSON" in capsys.readouterr().err


def test_refine_success_emits_diff(
    report_path: Path,
    prompt_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = (
        "--- prompt.txt\n"
        "+++ prompt.txt\n"
        "@@ -1 +1 @@\n"
        "-Extract the title from the document.\n"
        "+Extract the exact title from the document verbatim.\n"
    )

    def _fake_propose(**_kwargs: object) -> str:
        return diff

    monkeypatch.setattr("prompiler.cli.propose_patch_sync", _fake_propose)

    rc = main(["refine", str(report_path), str(prompt_path), "--backend", "mock"])

    assert rc == 0
    assert capsys.readouterr().out == diff
