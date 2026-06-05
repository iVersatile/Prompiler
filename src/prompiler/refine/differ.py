"""Diff applier — applies a tutor-proposed unified diff to the current prompt
text behind an explicit human-confirmation gate (PLAN.md L211, architecture.md
§2.6).

The applier is pure: it needs no backend. Application is driven by *context
matching*, not the ``@@`` header line-counts, because the tutor emits loose
headers (a ``@@ -1,3 +1,3 @@`` over a 2-line prompt). A diff whose context or
removed lines do not match the original, or one with no hunk header at all,
raises :class:`AdapterError` with the offending diff embedded (LL-003).

v1 never auto-applies: :func:`confirm_and_apply` validates the diff first, then
shows a preview and writes *only* when the confirm callback returns true.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from prompiler.runtime.errors import AdapterError

_HUNK_HEADER: Final = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _parse_hunks(diff: str) -> list[tuple[list[str], list[str]]]:
    """Split ``diff`` into (old, new) line sequences per hunk.

    ``old`` is the context + removed lines (what must match the original);
    ``new`` is the context + added lines (the replacement). File headers
    (``---``/``+++``) precede the first hunk header and are ignored.
    """
    hunks: list[tuple[list[str], list[str]]] = []
    current: tuple[list[str], list[str]] | None = None

    for line in diff.split("\n"):
        if _HUNK_HEADER.match(line):
            current = ([], [])
            hunks.append(current)
            continue
        if current is None or line == "":
            continue
        tag, body = line[0], line[1:]
        if tag == " ":
            current[0].append(body)
            current[1].append(body)
        elif tag == "-":
            current[0].append(body)
        elif tag == "+":
            current[1].append(body)

    return hunks


def _find_subsequence(haystack: list[str], needle: list[str], start: int) -> int | None:
    """Index of ``needle`` within ``haystack`` at or after ``start``, else None."""
    if not needle:
        return start
    last = len(haystack) - len(needle)
    for i in range(start, last + 1):
        if haystack[i : i + len(needle)] == needle:
            return i
    return None


def apply_patch(original: str, diff: str) -> str:
    """Apply unified ``diff`` to ``original`` by context matching.

    Raises :class:`AdapterError` (with the offending diff embedded — LL-003) when
    the diff carries no hunk header or its context/removed lines do not match.
    """
    hunks = _parse_hunks(diff)
    if not hunks:
        raise AdapterError(f"diff has no unified-diff hunk header:\n{diff}")

    result = original.split("\n")
    cursor = 0
    for old_seq, new_seq in hunks:
        idx = _find_subsequence(result, old_seq, cursor)
        if idx is None:
            raise AdapterError(f"diff context does not match the original:\n{diff}")
        result[idx : idx + len(old_seq)] = new_seq
        cursor = idx + len(new_seq)

    return "\n".join(result)


def render_diff_preview(diff: str) -> str:
    """Render a human-readable preview surfacing the hunks and changed lines."""
    kept = [
        line
        for line in diff.split("\n")
        if line.startswith(("@@", " ", "-", "+")) and not line.startswith(("---", "+++"))
    ]
    return "\n".join(kept)


def confirm_and_apply(
    *,
    original: str,
    diff: str,
    confirm: Callable[[str], bool],
    write: Callable[[str], None],
) -> bool:
    """Validate, preview, then write the patched text only if ``confirm`` allows.

    The diff is applied (and thus validated) *before* the user is prompted, so a
    malformed diff raises :class:`AdapterError` without ever calling ``confirm``
    or ``write``. Returns true when the patch was written, false on decline.
    """
    patched = apply_patch(original, diff)
    if not confirm(render_diff_preview(diff)):
        return False
    write(patched)
    return True
