"""Eval harness — fixtures, runner, metrics, reports (P4)."""

from __future__ import annotations

from prompiler.eval.fixtures import FixtureCase, load_fixtures
from prompiler.eval.metrics import DiffStatus, FieldScore, Metrics, aggregate, prf
from prompiler.eval.runner import CaseResult, EvalResult, FieldDiff, run_eval

__all__ = [
    "CaseResult",
    "DiffStatus",
    "EvalResult",
    "FieldDiff",
    "FieldScore",
    "FixtureCase",
    "Metrics",
    "aggregate",
    "load_fixtures",
    "prf",
    "run_eval",
]
