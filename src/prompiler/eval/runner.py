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

from prompiler.backends.observability import BackendCallMetrics
from prompiler.eval.fixtures import FixtureCase
from prompiler.eval.metrics import DiffStatus, Metrics, aggregate, jaccard
from prompiler.runtime.errors import PrompilerError
from prompiler.runtime.orchestrator import run_sync

FUZZY_THRESHOLD = 0.85


class CapturingHook:
    """Observability sink that records every ``BackendCallMetrics`` it receives.

    Wire one instance into the backend at construction *and* pass the same
    instance to ``run_eval`` as ``metrics_hook``. After the run, the runner
    folds the captured per-call metrics into ``EvalResult.usage``.

    Satisfies the ``ObservabilityHook`` Protocol structurally — no inheritance.
    """

    def __init__(self) -> None:
        self._calls: list[BackendCallMetrics] = []

    async def on_call(self, metrics: BackendCallMetrics) -> None:
        self._calls.append(metrics)

    @property
    def calls(self) -> tuple[BackendCallMetrics, ...]:
        return tuple(self._calls)


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
class EvalUsage:
    """Aggregate backend cost/token totals folded from captured call metrics."""

    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class EvalResult:
    """All case results plus the aggregate metrics over every observation.

    ``usage`` is ``None`` unless the caller wired a ``CapturingHook`` into the
    backend and passed it as ``metrics_hook`` — cost/token accounting is opt-in.
    """

    cases: tuple[CaseResult, ...]
    metrics: Metrics
    usage: EvalUsage | None = None
    fuzzy_metrics: Metrics | None = None


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


def _fuzzy_status(diff: FieldDiff) -> DiffStatus:
    """Upgrade a mismatch to match when its token-set Jaccard clears threshold."""
    if (
        diff.status == "mismatch"
        and diff.predicted is not None
        and jaccard(diff.expected, diff.predicted) >= FUZZY_THRESHOLD
    ):
        return "match"
    return diff.status


def _fuzzy_observations(
    case_results: Sequence[CaseResult],
) -> list[tuple[str, DiffStatus]]:
    """Build the fuzzy observation stream, arming the fallback per case.

    The fuzzy rescue fires only for a case whose *exact* F1 is 0.0 — i.e. the
    case had no true positive. Cases that already landed a match keep their
    exact diffs untouched so the fuzzy column never masks a partial hit.
    """
    fuzzy_obs: list[tuple[str, DiffStatus]] = []
    for case in case_results:
        case_exact = aggregate((d.field, d.status) for d in case.diffs)
        if case_exact.f1 == 0.0:
            fuzzy_obs.extend((d.field, _fuzzy_status(d)) for d in case.diffs)
        else:
            fuzzy_obs.extend((d.field, d.status) for d in case.diffs)
    return fuzzy_obs


def _summarize_usage(hook: CapturingHook) -> EvalUsage:
    calls = hook.calls
    return EvalUsage(
        calls=len(calls),
        prompt_tokens=sum(c.prompt_tokens for c in calls),
        completion_tokens=sum(c.completion_tokens for c in calls),
        cost_usd=sum(c.cost_usd for c in calls),
    )


def run_eval(
    spec_name: str,
    cases: Sequence[FixtureCase],
    *,
    backend: Any,
    registry: Any = None,
    timeout: float | None = None,
    metrics_hook: CapturingHook | None = None,
) -> EvalResult:
    """Run every fixture case through extraction and aggregate the metrics.

    Pass ``metrics_hook`` — the same ``CapturingHook`` instance wired into the
    backend at construction — to fold per-call cost/token metrics into
    ``EvalResult.usage``. When omitted, ``usage`` is ``None``.
    """
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
    usage = _summarize_usage(metrics_hook) if metrics_hook is not None else None
    any_failure = any(d.status != "match" for c in case_results for d in c.diffs)
    fuzzy_metrics = aggregate(_fuzzy_observations(case_results)) if any_failure else None
    return EvalResult(
        cases=tuple(case_results), metrics=metrics, usage=usage, fuzzy_metrics=fuzzy_metrics
    )
