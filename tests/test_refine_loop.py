"""Unit tests for the C1 auto-apply loop driver (``refine/loop.py``).

These tests pin the *pure* loop contract: ``run_refine_loop`` orchestrates
``propose -> apply -> evaluate`` for a bounded number of iterations, threading
the prompt and report between rounds and recording one ``LoopStep`` per round.

The driver is deliberately backend-agnostic — it takes three plain callables —
so this tier exercises it with scripted closures (zero network, zero registry,
zero filesystem). The C1 exit criterion ("a >=2-iteration scripted loop runs
end-to-end") is asserted directly here.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompiler.refine.loop import LoopResult, LoopStep, run_refine_loop


@pytest.mark.unit
def test_run_refine_loop_runs_two_iterations_end_to_end() -> None:
    # Arrange
    proposed_diffs = ["DIFF-1", "DIFF-2"]
    apply_calls: list[tuple[str, str]] = []
    eval_calls: list[str] = []
    f1_trajectory = [0.5, 1.0]

    def propose(report: dict[str, Any], prompt: str) -> str:
        return proposed_diffs[len(apply_calls)]

    def apply(prompt: str, diff: str) -> str:
        apply_calls.append((prompt, diff))
        return f"{prompt}+{diff}"

    def evaluate(prompt: str) -> tuple[float, dict[str, Any]]:
        eval_calls.append(prompt)
        f1 = f1_trajectory[len(eval_calls) - 1]
        return f1, {"aggregate": {"f1": f1}}

    # Act
    result = run_refine_loop(
        initial_prompt="P0",
        initial_report={"aggregate": {"f1": 0.0}},
        propose=propose,
        apply=apply,
        evaluate=evaluate,
        max_iterations=2,
    )

    # Assert: a >=2-iteration loop ran end-to-end
    assert isinstance(result, LoopResult)
    assert len(apply_calls) == 2
    assert len(eval_calls) == 2
    assert result.iterations == 2
    assert [s.iteration for s in result.steps] == [1, 2]
    assert [s.f1 for s in result.steps] == [0.5, 1.0]
    assert [s.diff for s in result.steps] == ["DIFF-1", "DIFF-2"]
    assert all(isinstance(s, LoopStep) for s in result.steps)
    # final_prompt reflects both applied diffs, in order
    assert result.final_prompt == "P0+DIFF-1+DIFF-2"


@pytest.mark.unit
def test_run_refine_loop_threads_report_between_iterations() -> None:
    """Round N's ``propose`` sees the report produced by round N-1's eval."""
    seen_reports: list[dict[str, Any]] = []

    def propose(report: dict[str, Any], prompt: str) -> str:
        seen_reports.append(report)
        return "D"

    def apply(prompt: str, diff: str) -> str:
        return prompt + diff

    def evaluate(prompt: str) -> tuple[float, dict[str, Any]]:
        n = len(seen_reports)
        return float(n), {"tag": n}

    run_refine_loop(
        initial_prompt="P",
        initial_report={"tag": "seed"},
        propose=propose,
        apply=apply,
        evaluate=evaluate,
        max_iterations=2,
    )

    assert seen_reports[0] == {"tag": "seed"}
    assert seen_reports[1] == {"tag": 1}


@pytest.mark.unit
def test_run_refine_loop_rejects_non_positive_max_iterations() -> None:
    def _unused_str(*_args: Any) -> str:  # pragma: no cover - never called
        return ""

    def _unused_eval(_prompt: str) -> tuple[float, dict[str, Any]]:  # pragma: no cover
        return 0.0, {}

    with pytest.raises(ValueError):
        run_refine_loop(
            initial_prompt="P",
            initial_report={},
            propose=_unused_str,
            apply=_unused_str,
            evaluate=_unused_eval,
            max_iterations=0,
        )
