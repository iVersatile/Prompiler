"""Performance-budget assertions for the hermetic hot paths (PRD §7.1).

Two budgets are network-free and therefore stable enough to gate in CI:

    | Operation              | Budget   |
    |------------------------|----------|
    | compile (single spec)  | < 200 ms |
    | validate (single spec) | <  50 ms |

The remaining PRD §7.1 budgets (`run`, `run_batch`, MCP cold start) depend on a
live backend or a spawned process; their timings are dominated by network and OS
scheduling, so asserting them as required checks would be flaky. They are
exercised manually per docs/MANUAL_TESTING.md instead.

Timing strategy: warm up once (to exclude import / first-call costs), then take
the **minimum** of N runs. The min is the least noisy estimator on shared CI
runners — scheduler jitter only ever inflates a sample, so the floor reflects the
true cost while staying robust to a single preempted iteration.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from prompiler.compiler import compile_spec
from prompiler.spec import EntitySpec, lint_spec, load_spec

pytestmark = pytest.mark.perf

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_EXAMPLE_PATHS = sorted(_EXAMPLES_DIR.glob("*.yaml"))
_EXAMPLE_IDS = [p.stem for p in _EXAMPLE_PATHS]

_COMPILE_BUDGET_MS = 200.0
_VALIDATE_BUDGET_MS = 50.0
_RUNS = 20

_T = TypeVar("_T")


def _best_ms(op: Callable[[], _T]) -> float:
    """Best-of-N wall-clock cost of ``op`` in milliseconds (one warm-up call)."""
    op()
    best = float("inf")
    for _ in range(_RUNS):
        start = time.perf_counter()
        op()
        best = min(best, time.perf_counter() - start)
    return best * 1000.0


@pytest.mark.parametrize("path", _EXAMPLE_PATHS, ids=_EXAMPLE_IDS)
def test_compile_within_budget(path: Path) -> None:
    spec = load_spec(path)
    elapsed_ms = _best_ms(lambda: compile_spec(spec))
    assert elapsed_ms < _COMPILE_BUDGET_MS, (
        f"compile {path.stem}: {elapsed_ms:.1f} ms exceeds {_COMPILE_BUDGET_MS:.0f} ms budget"
    )


@pytest.mark.parametrize("path", _EXAMPLE_PATHS, ids=_EXAMPLE_IDS)
def test_validate_within_budget(path: Path) -> None:
    def _validate() -> None:
        spec = load_spec(path)
        lint_spec(spec)

    elapsed_ms = _best_ms(_validate)
    assert elapsed_ms < _VALIDATE_BUDGET_MS, (
        f"validate {path.stem}: {elapsed_ms:.1f} ms exceeds {_VALIDATE_BUDGET_MS:.0f} ms budget"
    )


def test_examples_present_for_budgeting() -> None:
    assert _EXAMPLE_PATHS, f"no example specs to budget under {_EXAMPLES_DIR}"


def test_compiled_artefacts_are_well_formed() -> None:
    """Guard: a degenerate compile_spec that returns instantly would pass the
    budget vacuously. Confirm the timed call actually produces artefacts."""
    spec = load_spec(_EXAMPLE_PATHS[0])
    assert isinstance(spec, EntitySpec)
    bundle = compile_spec(spec)
    assert bundle.prompt
    assert bundle.spec_hash
