"""C1 auto-apply loop driver: ``propose → apply → evaluate``, bounded.

The driver is deliberately backend-agnostic. It takes three plain callables
and threads the prompt and report between rounds, recording one
:class:`LoopStep` per iteration. It performs no I/O of its own — disk writes
and stop-condition flags belong to later tasks (C3, C2 respectively).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["LoopResult", "LoopStep", "run_refine_loop"]


@dataclass(frozen=True)
class LoopStep:
    """One round of the auto-apply loop."""

    iteration: int
    f1: float
    diff: str


@dataclass(frozen=True)
class LoopResult:
    """Outcome of a bounded auto-apply run.

    ``stop_reason`` records why the loop halted:
    ``"threshold"`` (f1 reached ``threshold``), ``"stalled"`` (Δf1 between
    consecutive rounds fell below ``epsilon``), or ``"max_iterations"``
    (the round budget was exhausted without either condition firing).
    """

    final_prompt: str
    steps: tuple[LoopStep, ...]
    stop_reason: str

    @property
    def iterations(self) -> int:
        return len(self.steps)


def run_refine_loop(
    *,
    initial_prompt: str,
    initial_report: dict[str, Any],
    propose: Callable[[dict[str, Any], str], str],
    apply: Callable[[str, str], str],
    evaluate: Callable[[str], tuple[float, dict[str, Any]]],
    max_iterations: int,
    threshold: float = float("inf"),
    epsilon: float = 0.01,
) -> LoopResult:
    """Run ``propose → apply → evaluate`` for at most ``max_iterations`` rounds.

    Round N's ``propose`` sees the report produced by round N-1's ``evaluate``
    (round 1 sees ``initial_report``). The refined prompt is threaded forward.

    Two early-stop conditions are checked after each round, threshold first:
    if f1 reaches ``threshold`` the loop halts (``stop_reason="threshold"``);
    otherwise, from round 2 onward, if the gain over the previous round is
    below ``epsilon`` the loop halts (``stop_reason="stalled"``). Exhausting
    the budget without either firing yields ``stop_reason="max_iterations"``.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    prompt = initial_prompt
    report = initial_report
    steps: list[LoopStep] = []
    prev_f1: float | None = None
    stop_reason = "max_iterations"
    for iteration in range(1, max_iterations + 1):
        diff = propose(report, prompt)
        prompt = apply(prompt, diff)
        f1, report = evaluate(prompt)
        steps.append(LoopStep(iteration=iteration, f1=f1, diff=diff))
        if f1 >= threshold:
            stop_reason = "threshold"
            break
        if prev_f1 is not None and (f1 - prev_f1) < epsilon:
            stop_reason = "stalled"
            break
        prev_f1 = f1

    return LoopResult(final_prompt=prompt, steps=tuple(steps), stop_reason=stop_reason)
