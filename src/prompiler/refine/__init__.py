"""Refinement loop (P5): tutor → diff → re-eval.

The refine package turns an eval report into a proposed prompt edit. It does
not invent its own error type — per the sealed hierarchy (architecture.md
§1.3) it composes on :class:`prompiler.runtime.errors.AdapterError`.
"""

from __future__ import annotations

from prompiler.refine.differ import (
    apply_patch,
    confirm_and_apply,
    render_diff_preview,
)
from prompiler.refine.loop import (
    LoopResult,
    LoopStep,
    run_refine_loop,
)
from prompiler.refine.reeval import (
    FieldDelta,
    MetricDelta,
    ReevalDelta,
    compute_delta,
    render_delta,
)
from prompiler.refine.tutor import (
    TUTOR_RESPONSE_SCHEMA,
    build_tutor_user_prompt,
    propose_patch,
    propose_patch_sync,
)

__all__ = [
    "TUTOR_RESPONSE_SCHEMA",
    "FieldDelta",
    "LoopResult",
    "LoopStep",
    "MetricDelta",
    "ReevalDelta",
    "apply_patch",
    "build_tutor_user_prompt",
    "compute_delta",
    "confirm_and_apply",
    "propose_patch",
    "propose_patch_sync",
    "render_delta",
    "render_diff_preview",
    "run_refine_loop",
]
