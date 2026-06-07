"""Unit tests for the usage-log sink, reader, and summariser (P7 task 2)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from prompiler.backends.observability import BackendCallMetrics, ObservabilityHook
from prompiler.usage import (
    FileUsageHook,
    ModelUsage,
    UsageRecord,
    UsageSummary,
    default_usage_log_path,
    format_summary,
    parse_since,
    read_usage,
    summarize,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# parse_since
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", timedelta(days=7)),
        ("1d", timedelta(days=1)),
        ("24h", timedelta(hours=24)),
        ("30m", timedelta(minutes=30)),
        ("2w", timedelta(weeks=2)),
    ],
)
def test_parse_since_valid(text: str, expected: timedelta) -> None:
    assert parse_since(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "7", "d", "7x", "-1d", "abc", "7 d", "0d"],
)
def test_parse_since_invalid_raises(text: str) -> None:
    with pytest.raises(ValueError):
        parse_since(text)


# --------------------------------------------------------------------------- #
# FileUsageHook
# --------------------------------------------------------------------------- #
def _metrics(**over: object) -> BackendCallMetrics:
    base: dict[str, object] = {
        "backend": "claude",
        "model": "claude-haiku-4-5-20251001",
        "latency_seconds": 0.5,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost_usd": 0.0002,
    }
    base.update(over)
    return BackendCallMetrics(**base)  # type: ignore[arg-type]


def test_file_usage_hook_appends_jsonl(tmp_path) -> None:
    log = tmp_path / "nested" / "usage.jsonl"
    hook = FileUsageHook(log)

    asyncio.run(hook.on_call(_metrics(prompt_tokens=100)))
    asyncio.run(hook.on_call(_metrics(prompt_tokens=200)))

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["backend"] == "claude"
    assert first["model"] == "claude-haiku-4-5-20251001"
    assert first["prompt_tokens"] == 100
    assert first["completion_tokens"] == 20
    assert first["cost_usd"] == pytest.approx(0.0002)
    assert first["latency_seconds"] == pytest.approx(0.5)
    # ts must be ISO-8601 and round-trip to an aware UTC datetime.
    ts = datetime.fromisoformat(first["ts"])
    assert ts.tzinfo is not None


def test_file_usage_hook_satisfies_protocol(tmp_path) -> None:
    hook = FileUsageHook(tmp_path / "usage.jsonl")
    assert isinstance(hook, ObservabilityHook)


# --------------------------------------------------------------------------- #
# read_usage
# --------------------------------------------------------------------------- #
def test_read_usage_missing_file_returns_empty(tmp_path) -> None:
    assert read_usage(tmp_path / "absent.jsonl") == []


def test_read_usage_skips_blank_and_malformed(tmp_path) -> None:
    log = tmp_path / "usage.jsonl"
    good = {
        "ts": "2026-06-01T12:00:00+00:00",
        "backend": "openai",
        "model": "gpt-4o-mini",
        "latency_seconds": 0.3,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_usd": 0.001,
    }
    log.write_text(
        "\n".join(["", json.dumps(good), "   ", "{not json", ""]) + "\n",
        encoding="utf-8",
    )

    records = read_usage(log)
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, UsageRecord)
    assert rec.backend == "openai"
    assert rec.model == "gpt-4o-mini"
    assert rec.prompt_tokens == 10
    assert rec.ts.tzinfo is not None


# --------------------------------------------------------------------------- #
# summarize
# --------------------------------------------------------------------------- #
def _record(ts: datetime, **over: object) -> UsageRecord:
    base: dict[str, object] = {
        "ts": ts,
        "backend": "claude",
        "model": "claude-haiku-4-5-20251001",
        "latency_seconds": 0.5,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost_usd": 0.001,
    }
    base.update(over)
    return UsageRecord(**base)  # type: ignore[arg-type]


def test_summarize_empty_returns_zeroed() -> None:
    now = datetime(2026, 6, 7, tzinfo=UTC)
    summary = summarize([], since=timedelta(days=7), now=now)
    assert isinstance(summary, UsageSummary)
    assert summary.calls == 0
    assert summary.prompt_tokens == 0
    assert summary.completion_tokens == 0
    assert summary.cost_usd == 0.0
    assert summary.by_model == ()


def test_summarize_filters_by_since_window() -> None:
    now = datetime(2026, 6, 7, tzinfo=UTC)
    inside = _record(now - timedelta(days=1))
    outside = _record(now - timedelta(days=30))

    summary = summarize([inside, outside], since=timedelta(days=7), now=now)
    assert summary.calls == 1
    assert summary.prompt_tokens == 100


def test_summarize_aggregates_and_breaks_down_by_model() -> None:
    now = datetime(2026, 6, 7, tzinfo=UTC)
    records = [
        _record(now - timedelta(hours=1), backend="claude", model="claude-haiku-4-5-20251001"),
        _record(now - timedelta(hours=2), backend="claude", model="claude-haiku-4-5-20251001"),
        _record(
            now - timedelta(hours=3),
            backend="openai",
            model="gpt-4o-mini",
            prompt_tokens=50,
            completion_tokens=10,
            cost_usd=0.002,
        ),
    ]
    summary = summarize(records, since=timedelta(days=7), now=now)

    assert summary.calls == 3
    assert summary.prompt_tokens == 100 + 100 + 50
    assert summary.completion_tokens == 20 + 20 + 10
    assert summary.cost_usd == pytest.approx(0.001 + 0.001 + 0.002)

    assert len(summary.by_model) == 2
    assert all(isinstance(m, ModelUsage) for m in summary.by_model)
    by_key = {(m.backend, m.model): m for m in summary.by_model}
    claude = by_key[("claude", "claude-haiku-4-5-20251001")]
    assert claude.calls == 2
    assert claude.prompt_tokens == 200
    openai = by_key[("openai", "gpt-4o-mini")]
    assert openai.calls == 1
    assert openai.cost_usd == pytest.approx(0.002)


# --------------------------------------------------------------------------- #
# format_summary
# --------------------------------------------------------------------------- #
def test_format_summary_mentions_totals() -> None:
    now = datetime(2026, 6, 7, tzinfo=UTC)
    summary = summarize([_record(now - timedelta(hours=1))], since=timedelta(days=7), now=now)
    text = format_summary(summary)
    assert "1" in text  # one call
    assert "claude-haiku-4-5-20251001" in text


def test_format_summary_empty_is_readable() -> None:
    now = datetime(2026, 6, 7, tzinfo=UTC)
    summary = summarize([], since=timedelta(days=7), now=now)
    text = format_summary(summary)
    assert text.strip() != ""


# --------------------------------------------------------------------------- #
# default_usage_log_path
# --------------------------------------------------------------------------- #
def test_default_usage_log_path_honors_env(monkeypatch, tmp_path) -> None:
    target = tmp_path / "custom" / "u.jsonl"
    monkeypatch.setenv("PROMPILER_USAGE_LOG", str(target))
    assert default_usage_log_path() == target


def test_default_usage_log_path_fallback(monkeypatch) -> None:
    monkeypatch.delenv("PROMPILER_USAGE_LOG", raising=False)
    path = default_usage_log_path()
    assert path.name == "usage.jsonl"
    assert path.parent.name == ".prompiler"
