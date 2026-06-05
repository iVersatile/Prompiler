"""Patch generator — feeds an eval report + the current prompt to a "tutor"
backend call and returns a unified diff over the prompt text (PLAN.md L210,
architecture.md §2.6).

The tutor never edits files itself; it only proposes a diff. Applying the diff
(with explicit human confirmation) is the differ's job. On refusal, an empty
diff, or a malformed diff the tutor raises :class:`AdapterError` with the
offending reason/diff embedded so the failure is debuggable (LL-003).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Final

from prompiler.backends.base import BackendAdapter
from prompiler.runtime.errors import AdapterError

# Response contract the tutor backend must satisfy. ``decline`` is the only
# required key so a backend can refuse cleanly without fabricating a diff.
TUTOR_RESPONSE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "decline": {"type": "boolean"},
        "reason": {"type": "string"},
        "diff": {"type": "string"},
    },
    "required": ["decline"],
    "additionalProperties": False,
}

_TUTOR_SYSTEM_PROMPT: Final[str] = (
    "You are a prompt-refinement tutor. You are given a prompt used for "
    "structured extraction and an evaluation report scoring that prompt's "
    "output against gold labels. Propose the smallest edit to the prompt that "
    "raises extraction quality. Respond with a single unified diff (`---`/`+++`/"
    "`@@` hunks) over the prompt text and nothing else. If the prompt is already "
    "optimal or you cannot improve it, set `decline` to true and explain why in "
    "`reason` instead of inventing a diff."
)

# A real unified diff carries at least one hunk header of this shape.
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


def _looks_like_unified_diff(diff: str) -> bool:
    """True when ``diff`` contains a unified-diff hunk header."""
    return _HUNK_HEADER.search(diff) is not None


def build_tutor_user_prompt(report: dict[str, Any], current_prompt: str) -> str:
    """Render the tutor user prompt embedding the current prompt + report metrics.

    The current prompt is fenced verbatim so the tutor can diff against the exact
    text; aggregate and per-field metrics are summarised so it knows what to fix.
    """
    aggregate = report.get("aggregate", {})
    agg_lines = "\n".join(f"  {key}: {value}" for key, value in aggregate.items())

    per_field = report.get("per_field", {})
    field_lines = "\n".join(
        f"  {field}: p={scores.get('p')} r={scores.get('r')} f1={scores.get('f1')}"
        for field, scores in per_field.items()
    )

    return (
        "## Current prompt\n"
        f"```\n{current_prompt}\n```\n\n"
        "## Evaluation report\n"
        f"spec: {report.get('spec')}\n"
        f"backend: {report.get('backend')}  model: {report.get('model')}\n"
        "aggregate:\n"
        f"{agg_lines}\n"
        "per_field:\n"
        f"{field_lines}\n\n"
        "Propose a unified diff over the current prompt that improves these "
        "metrics, or decline."
    )


async def propose_patch(
    *,
    report: dict[str, Any],
    current_prompt: str,
    backend: BackendAdapter,
    timeout: float | None = None,
) -> str:
    """Ask ``backend`` for a unified diff over ``current_prompt``.

    Raises :class:`AdapterError` if the tutor declines, returns no diff, or
    returns text that is not a unified diff (offending payload embedded — LL-003).
    """
    user_prompt = build_tutor_user_prompt(report, current_prompt)
    response = await backend.extract(
        prompt=user_prompt,
        json_schema=TUTOR_RESPONSE_SCHEMA,
        timeout=timeout,
    )

    if response.get("decline"):
        # Distinguish absent/null/blank reason from a real one (LL-003): only
        # fall back to the placeholder when there is nothing meaningful to show,
        # so a present non-blank reason survives verbatim into the error.
        raw_reason = response.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        if not reason:
            reason = "(no reason given)"
        raise AdapterError(f"tutor declined to propose a patch: {reason}")

    diff: str = response.get("diff", "")
    if not diff.strip():
        raise AdapterError("tutor returned an empty diff")

    if not _looks_like_unified_diff(diff):
        raise AdapterError(f"tutor returned a malformed (non-unified) diff:\n{diff}")

    return diff


def propose_patch_sync(
    *,
    report: dict[str, Any],
    current_prompt: str,
    backend: BackendAdapter,
    timeout: float | None = None,
) -> str:
    """Sync wrapper over :func:`propose_patch` via :func:`asyncio.run`.

    The coroutine is closed in ``finally`` so it is never orphaned when
    ``asyncio.run`` cannot drive it (e.g. raised because a loop is already
    running); on the success path the coroutine is already finished and
    ``close()`` is a no-op.
    """
    coro = propose_patch(
        report=report,
        current_prompt=current_prompt,
        backend=backend,
        timeout=timeout,
    )
    try:
        return asyncio.run(coro)
    finally:
        coro.close()
