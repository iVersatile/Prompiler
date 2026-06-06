"""Tests for the diff applier — ``refine/differ.py`` (P5, PLAN.md L211).

The differ consumes a unified diff (produced by the tutor) and applies it to the
current prompt text. It never auto-applies: a confirmation callback gates every
write (architecture.md §2.6, "v1 never auto-applies"). These tests pin:

- a valid unified diff applies cleanly to the original text;
- header line-counts are advisory — application is driven by context matching,
  so a slightly-off ``@@ -1,3`` header (as the tutor fixture emits) still applies;
- a diff whose context/removed lines do not match the original raises
  ``AdapterError`` with the offending diff embedded (LL-003);
- a diff with no hunk header raises ``AdapterError``;
- the preview renderer surfaces the added/removed lines;
- ``confirm_and_apply`` shows the preview, and writes *only* when the confirm
  callback returns true — declining never calls the write sink.
"""

from __future__ import annotations

import pytest

from prompiler.refine.differ import apply_patch, confirm_and_apply, render_diff_preview
from prompiler.runtime.errors import AdapterError

_ORIGINAL = "Extract the fields.\nReturn JSON."

# Mirrors the tutor's _VALID_DIFF: note the ``@@ -1,3 +1,3 @@`` header over a
# 2-line original — counts are deliberately loose, application is by context.
_VALID_DIFF = (
    "--- a/prompt.txt\n"
    "+++ b/prompt.txt\n"
    "@@ -1,3 +1,3 @@\n"
    " Extract the fields.\n"
    "-Return JSON.\n"
    "+Return strict JSON only.\n"
)

_PATCHED = "Extract the fields.\nReturn strict JSON only."


def test_apply_patch_replaces_line_by_context() -> None:
    assert apply_patch(_ORIGINAL, _VALID_DIFF) == _PATCHED


def test_apply_patch_pure_insertion() -> None:
    diff = (
        "--- a/prompt.txt\n"
        "+++ b/prompt.txt\n"
        "@@ -1,1 +1,2 @@\n"
        " Extract the fields.\n"
        "+Return strict JSON only.\n"
    )

    assert apply_patch("Extract the fields.", diff) == (
        "Extract the fields.\nReturn strict JSON only."
    )


def test_apply_patch_raises_on_context_mismatch_embedding_diff() -> None:
    diff = (
        "--- a/prompt.txt\n"
        "+++ b/prompt.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " A line that is not in the original.\n"
        "-Return JSON.\n"
        "+Return strict JSON only.\n"
    )

    with pytest.raises(AdapterError) as excinfo:
        apply_patch(_ORIGINAL, diff)

    assert "not in the original" in str(excinfo.value)


def test_apply_patch_raises_on_missing_hunk_header() -> None:
    diff = "--- a/prompt.txt\n+++ b/prompt.txt\njust prose, no hunk\n"

    with pytest.raises(AdapterError) as excinfo:
        apply_patch(_ORIGINAL, diff)

    assert "just prose, no hunk" in str(excinfo.value)


def test_render_diff_preview_surfaces_changed_lines() -> None:
    preview = render_diff_preview(_VALID_DIFF)

    assert "-Return JSON." in preview
    assert "+Return strict JSON only." in preview


class _Sink:
    """Records whether (and what) was written so a decline can assert no write."""

    def __init__(self) -> None:
        self.written: str | None = None

    def write(self, text: str) -> None:
        self.written = text


def test_confirm_and_apply_writes_patched_text_when_confirmed() -> None:
    sink = _Sink()
    seen_preview: list[str] = []

    def confirm(preview: str) -> bool:
        seen_preview.append(preview)
        return True

    applied = confirm_and_apply(
        original=_ORIGINAL, diff=_VALID_DIFF, confirm=confirm, write=sink.write
    )

    assert applied is True
    assert sink.written == _PATCHED
    assert seen_preview and "+Return strict JSON only." in seen_preview[0]


def test_confirm_and_apply_never_writes_when_declined() -> None:
    sink = _Sink()

    applied = confirm_and_apply(
        original=_ORIGINAL,
        diff=_VALID_DIFF,
        confirm=lambda _preview: False,
        write=sink.write,
    )

    assert applied is False
    assert sink.written is None


def test_confirm_and_apply_validates_before_prompting() -> None:
    """A malformed diff raises before the user is ever asked to confirm."""
    sink = _Sink()
    prompted = False

    def confirm(_preview: str) -> bool:
        nonlocal prompted
        prompted = True
        return True

    with pytest.raises(AdapterError):
        confirm_and_apply(
            original=_ORIGINAL,
            diff="no hunk header here\n",
            confirm=confirm,
            write=sink.write,
        )

    assert prompted is False
    assert sink.written is None
