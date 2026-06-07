"""CLI tests for ``prompiler stats`` (P7, usage summary over a time window).

The stats subcommand reads the local JSONL usage log, filters by ``--since``,
and prints an aggregate summary. These tests drive that contract via
``main([...])`` and assert process exit codes (0 ok, 1 invalid ``--since``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from prompiler.cli import main

pytestmark = pytest.mark.unit


def _write_log(path: Path, *records: dict[str, object]) -> None:
    lines = [json.dumps(rec, separators=(",", ":")) for rec in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _record(**over: object) -> dict[str, object]:
    rec: dict[str, object] = {
        "ts": datetime.now(UTC).isoformat(),
        "backend": "claude",
        "model": "claude-haiku-4-5-20251001",
        "latency_seconds": 0.5,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost_usd": 0.0002,
    }
    rec.update(over)
    return rec


def test_stats_missing_log_returns_0_and_reports_no_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["stats", "--since", "7d", "--log", str(tmp_path / "nope.jsonl")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usage over the last 7d:" in out
    assert "No calls recorded in this window." in out


def test_stats_invalid_since_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["stats", "--since", "7x", "--log", str(tmp_path / "usage.jsonl")])
    assert rc == 1
    assert "invalid --since value" in capsys.readouterr().err


def test_stats_summarises_recent_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "usage.jsonl"
    _write_log(
        log,
        _record(prompt_tokens=100, completion_tokens=20, cost_usd=0.001),
        _record(prompt_tokens=200, completion_tokens=30, cost_usd=0.002),
    )
    rc = main(["stats", "--since", "7d", "--log", str(log)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "calls:             2" in out
    assert "prompt tokens:     300" in out
    assert "completion tokens: 50" in out
    assert "claude/claude-haiku-4-5-20251001:" in out


def test_stats_filters_out_records_before_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "usage.jsonl"
    old = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    _write_log(log, _record(ts=old))
    rc = main(["stats", "--since", "7d", "--log", str(log)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "calls:             0" in out
    assert "No calls recorded in this window." in out


def test_stats_skips_malformed_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "usage.jsonl"
    good = json.dumps(_record(), separators=(",", ":"))
    log.write_text(f"\n{{not json\n{good}\n", encoding="utf-8")
    rc = main(["stats", "--since", "7d", "--log", str(log)])
    assert rc == 0
    assert "calls:             1" in capsys.readouterr().out
