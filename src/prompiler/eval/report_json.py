"""``eval-report.json`` emitter — serialize an eval run to the canonical schema (P4).

Pure serializer: takes an :class:`~prompiler.eval.runner.EvalResult` plus run
metadata (spec name, spec_hash, backend, model, timestamp, fixture path) supplied
by the caller and produces the JSON document defined in ``docs/architecture.md``.
It does *not* reach into the backend or registry — mirroring the B1 philosophy of
the runner. The future ``prompiler eval`` CLI sources ``spec_hash`` via
``registry.get(spec_name).spec_hash`` and passes backend/model itself.

Schema (docs/architecture.md):

    {
      "spec": "...", "spec_hash": "...", "backend": "...", "model": "...",
      "timestamp": "2026-05-21T01:38:00Z", "fixture_path": "...",
      "aggregate": { "precision":, "recall":, "f1": },
      "per_field":  { "<field>": { "p":, "r":, "f1": }, ... },
      "per_case":   [ { "name":, "diff": [...], "ok": }, ... ],
      "usage":      { "input_tokens":, "output_tokens":, "cost_estimate_usd": }
    }

``timestamp`` is emitted as ISO 8601 UTC with a trailing ``Z`` and second
precision. ``usage`` is ``null`` unless the run captured cost/token metrics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prompiler.eval.runner import CaseResult, EvalResult

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _format_timestamp(timestamp: datetime | None) -> str:
    dt = timestamp if timestamp is not None else datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


def _case_ok(case: CaseResult) -> bool:
    return case.error is None and all(d.status == "match" for d in case.diffs)


def _serialize_case(case: CaseResult) -> dict[str, Any]:
    return {
        "name": case.name,
        "diff": [
            {
                "field": d.field,
                "expected": d.expected,
                "predicted": d.predicted,
                "status": d.status,
            }
            for d in case.diffs
        ],
        "ok": _case_ok(case),
    }


def build_report(
    result: EvalResult,
    *,
    spec: str,
    spec_hash: str,
    backend: str,
    model: str,
    timestamp: datetime | None = None,
    fixture_path: str | None = None,
) -> dict[str, Any]:
    """Map an ``EvalResult`` plus run metadata onto the eval-report JSON schema.

    ``spec_hash`` is written verbatim — the caller decides any prefixing.
    ``timestamp`` defaults to the current UTC time when omitted.
    """
    metrics = result.metrics
    usage = result.usage
    return {
        "spec": spec,
        "spec_hash": spec_hash,
        "backend": backend,
        "model": model,
        "timestamp": _format_timestamp(timestamp),
        "fixture_path": fixture_path,
        "aggregate": {
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
        },
        "per_field": {
            field: {"p": score.precision, "r": score.recall, "f1": score.f1}
            for field, score in metrics.per_field.items()
        },
        "per_case": [_serialize_case(c) for c in result.cases],
        "usage": (
            None
            if usage is None
            else {
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "cost_estimate_usd": usage.cost_usd,
            }
        ),
    }


def dump_report(report: dict[str, Any], *, indent: int = 2) -> str:
    """Serialize a report dict to a deterministic JSON string."""
    return json.dumps(report, indent=indent, ensure_ascii=False, sort_keys=False)


def write_report(report: dict[str, Any], path: str | Path, *, indent: int = 2) -> None:
    """Write a report dict to ``path`` as UTF-8 JSON with a trailing newline."""
    Path(path).write_text(dump_report(report, indent=indent) + "\n", encoding="utf-8")
