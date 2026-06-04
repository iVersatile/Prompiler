"""Eval runner — drive fixtures through extraction and score them (P4).

Iterates fixture cases, runs each through the orchestrator's extraction
pipeline, diffs the predicted fields against the case's ``expected`` map, and
folds every observation into precision / recall / F1 via ``eval.metrics``.

Scoring is restricted to the fields a fixture *declares* in ``expected``: a
fixture lists only the slots it cares about, so counting other predicted keys as
false positives would poison precision. Consequently the runner only ever emits
``match`` / ``mismatch`` / ``missing`` statuses; ``extra`` is reserved by
metrics.py for callers that want full-surface scoring.

Per-case extraction errors are isolated: a ``PrompilerError`` raised for one case
is recorded on its ``CaseResult`` and its expected fields count as ``missing``
(false negatives) so one broken case does not abort the whole run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from prompiler.eval.fixtures import FixtureCase
from prompiler.eval.metrics import DiffStatus, Metrics, aggregate
from prompiler.runtime.errors import PrompilerError
from prompiler.runtime.orchestrator import run_sync


@dataclass(frozen=True)
class FieldDiff:
    """One field's expected vs. predicted value and the resulting status."""

    field: str
    expected: str
    predicted: str | None
    status: DiffStatus


@dataclass(frozen=True)
class CaseResult:
    """Per-case field diffs; ``error`` set when extraction raised."""

    name: str
    diffs: tuple[FieldDiff, ...]
    error: str | None = None


@dataclass(frozen=True)
class EvalResult:
    """All case results plus the aggregate metrics over every observation."""

    cases: tuple[CaseResult, ...]
    metrics: Metrics


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _diff_field(field: str, expected: Any, predicted: Any) -> FieldDiff:
    exp_norm = _norm(expected)
    pred_norm = _norm(predicted)
    predicted_str = None if predicted is None else str(predicted)
    status: DiffStatus
    if pred_norm == "":
        status = "missing"
    elif pred_norm == exp_norm:
        status = "match"
    else:
        status = "mismatch"
    return FieldDiff(field=field, expected=str(expected), predicted=predicted_str, status=status)


def _diff_case(expected: Mapping[str, Any], predicted: Mapping[str, Any]) -> tuple[FieldDiff, ...]:
    return tuple(
        _diff_field(field, exp_val, predicted.get(field)) for field, exp_val in expected.items()
    )


def _error_diffs(expected: Mapping[str, Any]) -> tuple[FieldDiff, ...]:
    return tuple(
        FieldDiff(field=field, expected=str(exp_val), predicted=None, status="missing")
        for field, exp_val in expected.items()
    )


def run_eval(
    spec_name: str,
    cases: Sequence[FixtureCase],
    *,
    backend: Any,
    registry: Any = None,
    timeout: float | None = None,
) -> EvalResult:
    """Run every fixture case through extraction and aggregate the metrics."""
    case_results: list[CaseResult] = []
    observations: list[tuple[str, DiffStatus]] = []
    for case in cases:
        try:
            result = run_sync(
                spec_name, case.input, backend=backend, registry=registry, timeout=timeout
            )
        except PrompilerError as exc:
            diffs = _error_diffs(case.expected)
            case_results.append(CaseResult(name=case.name, diffs=diffs, error=str(exc)))
        else:
            predicted = result.model_dump()
            diffs = _diff_case(case.expected, predicted)
            case_results.append(CaseResult(name=case.name, diffs=diffs))
        observations.extend((d.field, d.status) for d in case_results[-1].diffs)
    metrics = aggregate(observations)
    return EvalResult(cases=tuple(case_results), metrics=metrics)
