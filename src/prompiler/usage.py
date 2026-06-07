"""Local usage-log sink, reader, and summariser for ``prompiler stats``.

The backend adapters emit :class:`BackendCallMetrics` through an
:class:`ObservabilityHook`. :class:`FileUsageHook` persists those metrics as
JSON-Lines so they can be queried after the fact. ``prompiler stats`` reads the
log, filters by a ``--since`` window, and prints an aggregate summary.

Only usage metrics (tokens, cost, latency, model) are recorded here — never
prompt or response payloads — so this sink is policy-safe at any log level.
"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from prompiler.backends.observability import BackendCallMetrics

DEFAULT_USAGE_LOG = Path(".prompiler") / "usage.jsonl"
_ENV_USAGE_LOG = "PROMPILER_USAGE_LOG"

_SINCE_UNITS: dict[str, str] = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}
_SINCE_RE = re.compile(r"^(\d+)([mhdw])$")


@dataclass(frozen=True)
class UsageRecord:
    """A single persisted backend call."""

    ts: datetime
    backend: str
    model: str
    latency_seconds: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ModelUsage:
    """Per-(backend, model) rollup."""

    backend: str
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class UsageSummary:
    """Aggregate over a time window, with a per-model breakdown."""

    since: timedelta
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    by_model: tuple[ModelUsage, ...]


def default_usage_log_path() -> Path:
    """Resolve the usage-log path from ``PROMPILER_USAGE_LOG`` or the default."""
    override = os.environ.get(_ENV_USAGE_LOG)
    if override:
        return Path(override)
    return DEFAULT_USAGE_LOG


def parse_since(text: str) -> timedelta:
    """Parse a ``--since`` token like ``7d`` / ``24h`` / ``30m`` / ``2w``.

    Raises:
        ValueError: if the token is empty, malformed, or non-positive.
    """
    match = _SINCE_RE.match(text)
    if match is None:
        raise ValueError(f"invalid --since value: {text!r} (expected e.g. 7d, 24h, 30m, 2w)")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError(f"--since value must be positive: {text!r}")
    unit = _SINCE_UNITS[match.group(2)]
    return timedelta(**{unit: amount})


class FileUsageHook:
    """:class:`ObservabilityHook` that appends each call to a JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def on_call(self, metrics: BackendCallMetrics) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "backend": metrics.backend,
            "model": metrics.model,
            "latency_seconds": metrics.latency_seconds,
            "prompt_tokens": metrics.prompt_tokens,
            "completion_tokens": metrics.completion_tokens,
            "cost_usd": metrics.cost_usd,
        }
        line = json.dumps(record, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _parse_record(payload: dict[str, object]) -> UsageRecord:
    return UsageRecord(
        ts=datetime.fromisoformat(str(payload["ts"])),
        backend=str(payload["backend"]),
        model=str(payload["model"]),
        latency_seconds=float(cast(float, payload["latency_seconds"])),
        prompt_tokens=int(cast(int, payload["prompt_tokens"])),
        completion_tokens=int(cast(int, payload["completion_tokens"])),
        cost_usd=float(cast(float, payload["cost_usd"])),
    )


def read_usage(path: Path) -> list[UsageRecord]:
    """Read all parseable records from ``path``; missing file yields ``[]``.

    Blank lines and malformed JSON lines are skipped rather than fatal — a
    truncated tail write should not break ``stats``.
    """
    if not path.exists():
        return []
    records: list[UsageRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
            records.append(_parse_record(payload))
        except (ValueError, KeyError, TypeError):
            continue
    return records


def summarize(
    records: Iterable[UsageRecord],
    *,
    since: timedelta,
    now: datetime,
) -> UsageSummary:
    """Aggregate records within ``[now - since, now]`` with a per-model breakdown."""
    cutoff = now - since
    calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    buckets: OrderedDict[tuple[str, str], dict[str, float]] = OrderedDict()

    for rec in records:
        if rec.ts < cutoff or rec.ts > now:
            continue
        calls += 1
        prompt_tokens += rec.prompt_tokens
        completion_tokens += rec.completion_tokens
        cost_usd += rec.cost_usd

        key = (rec.backend, rec.model)
        bucket = buckets.setdefault(
            key,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += rec.prompt_tokens
        bucket["completion_tokens"] += rec.completion_tokens
        bucket["cost_usd"] += rec.cost_usd

    by_model = tuple(
        ModelUsage(
            backend=backend,
            model=model,
            calls=int(b["calls"]),
            prompt_tokens=int(b["prompt_tokens"]),
            completion_tokens=int(b["completion_tokens"]),
            cost_usd=b["cost_usd"],
        )
        for (backend, model), b in buckets.items()
    )
    return UsageSummary(
        since=since,
        calls=calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        by_model=by_model,
    )


def format_summary(summary: UsageSummary) -> str:
    """Render a human-readable text summary."""
    lines = [
        f"Usage over the last {_format_window(summary.since)}:",
        f"  calls:             {summary.calls}",
        f"  prompt tokens:     {summary.prompt_tokens}",
        f"  completion tokens: {summary.completion_tokens}",
        f"  cost (USD):        ${summary.cost_usd:.4f}",
    ]
    if summary.by_model:
        lines.append("")
        lines.append("By model:")
        for m in _sorted_models(summary.by_model):
            lines.append(
                f"  {m.backend}/{m.model}: "
                f"{m.calls} calls, "
                f"{m.prompt_tokens + m.completion_tokens} tokens, "
                f"${m.cost_usd:.4f}"
            )
    else:
        lines.append("")
        lines.append("No calls recorded in this window.")
    return "\n".join(lines)


def _sorted_models(models: Sequence[ModelUsage]) -> list[ModelUsage]:
    return sorted(models, key=lambda m: m.cost_usd, reverse=True)


def _format_window(since: timedelta) -> str:
    total_seconds = int(since.total_seconds())
    days, rem = divmod(total_seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "0m"
