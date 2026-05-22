#!/usr/bin/env python3
"""Commit-msg hook: enforce LESSONS_LEARNT citation on fix:/perf: commits.

Source of truth: docs/RULES.md §4.

A commit whose subject begins with `fix:` or `perf:` (with optional scope and
breaking-change `!`) must either:

  * Cite an applied lesson via the literal pattern ``Applying LL-NNN`` in the
    body (NNN = three digits), OR
  * Acknowledge no applicable lesson via a trailer of the form
    ``Lesson-skip: <reason>`` where ``<reason>`` is at least 10 characters of
    plain explanation.

Subjects that do not start with `fix:` or `perf:` are accepted unconditionally
— feat/docs/test/chore/refactor/ci/release commits do not require a lesson
citation.

Usage (wired by `.pre-commit-config.yaml` under the `commit-msg` stage):

    python scripts/check_lesson_cite.py <path-to-COMMIT_EDITMSG>

Exit codes:
    0  citation present, or commit is not a fix/perf
    1  citation missing on a fix/perf commit (prints actionable guidance)
    2  invocation error (missing argument, unreadable file)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SUBJECT_TRIGGER = re.compile(r"^(fix|perf)(\([^)]+\))?!?:")
LESSON_CITATION = re.compile(r"\bApplying LL-\d{3}\b")
LESSON_SKIP = re.compile(r"^Lesson-skip:\s*(.{10,})$", re.MULTILINE)
LESSON_SKIP_BARE = re.compile(r"^Lesson-skip:\s*(.*)$", re.MULTILINE)


def _staged_is_docs_only() -> bool:
    """Return True if every staged path ends in ``.md``.

    Docs-only commits relax the ``Lesson-skip`` trailer minimum length from
    10 chars to 0 (see docs/RULES.md §4). A bare ``Lesson-skip:`` is enough.
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    paths = [line for line in result.stdout.splitlines() if line]
    if not paths:
        return False
    return all(p.lower().endswith(".md") for p in paths)


def _read_message(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check_lesson_cite: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _strip_comments(message: str) -> str:
    """Drop git's `#`-prefixed scissor/instruction lines from the message."""
    return "\n".join(line for line in message.splitlines() if not line.startswith("#"))


def _subject_line(message: str) -> str:
    for line in message.splitlines():
        if line.strip():
            return line
    return ""


def _fail(reason: str) -> None:
    sys.stderr.write(
        "\n"
        "Commit rejected: missing lesson citation.\n"
        "\n"
        f"  {reason}\n"
        "\n"
        "Required for commits whose subject starts with `fix:` or `perf:`.\n"
        "See docs/RULES.md §4 (LESSONS_LEARNT).\n"
        "\n"
        "Resolve one of two ways:\n"
        "\n"
        "  1. Cite the applied lesson in the commit body:\n"
        "\n"
        "       Applying LL-007\n"
        "\n"
        "     Run scripts/new_lesson.py if no matching lesson exists yet —\n"
        "     append a new entry to docs/LESSONS_LEARNT.md first, then cite it.\n"
        "\n"
        "  2. If this fix genuinely has no transferable lesson, add a trailer:\n"
        "\n"
        "       Lesson-skip: <at least 10 chars explaining why no lesson applies>\n"
        "\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("check_lesson_cite: expected path to commit message", file=sys.stderr)
        return 2

    path = Path(argv[1])
    raw = _read_message(path)
    message = _strip_comments(raw)

    subject = _subject_line(message)
    if not SUBJECT_TRIGGER.match(subject):
        return 0

    if LESSON_CITATION.search(message):
        return 0
    if LESSON_SKIP.search(message):
        return 0
    if _staged_is_docs_only() and LESSON_SKIP_BARE.search(message):
        return 0

    _fail(f"Subject `{subject}` triggers lesson-citation rule.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
