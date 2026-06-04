"""Eval harness — fixtures, runner, metrics, reports (P4)."""

from __future__ import annotations

from prompiler.eval.fixtures import FixtureCase, load_fixtures
from prompiler.eval.metrics import DiffStatus, FieldScore, Metrics, aggregate, prf
from prompiler.eval.report_json import build_report, dump_report, write_report
from prompiler.eval.runner import (
    CapturingHook,
    CaseResult,
    EvalResult,
    EvalUsage,
    FieldDiff,
    run_eval,
)

__all__ = [
    "CapturingHook",
    "CaseResult",
    "DiffStatus",
    "EvalResult",
    "EvalUsage",
    "FieldDiff",
    "FieldScore",
    "FixtureCase",
    "Metrics",
    "aggregate",
    "build_report",
    "dump_report",
    "load_fixtures",
    "prf",
    "run_eval",
    "write_report",
]
