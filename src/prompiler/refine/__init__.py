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
from prompiler.refine.tutor import (
    TUTOR_RESPONSE_SCHEMA,
    build_tutor_user_prompt,
    propose_patch,
    propose_patch_sync,
)

__all__ = [
    "TUTOR_RESPONSE_SCHEMA",
    "apply_patch",
    "build_tutor_user_prompt",
    "confirm_and_apply",
    "propose_patch",
    "propose_patch_sync",
    "render_diff_preview",
]
