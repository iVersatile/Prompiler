#!/usr/bin/env python3
"""Prepare-commit-msg hook: append a Lesson-skip hint for docs-only commits.

Source of truth: docs/RULES.md §4.
Wired in .pre-commit-config.yaml hook id `prepare-commit-msg-docs-hint`.

If all staged changes consist exclusively of Markdown (*.md) files, this hook
appends a commented hint to the commit message template suggesting the
Lesson-skip trailer to bypass the citation gate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HINT_MARKER = "# prompiler: docs-only diff detected"


def _get_staged_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        sys.stderr.write(f"prepare_commit_msg: failed to get staged files: {exc}\n")
        return []


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("prepare_commit_msg: expected path to commit message file\n")
        return 2

    commit_msg_file = Path(argv[1])
    if not commit_msg_file.is_file():
        sys.stderr.write(f"prepare_commit_msg: file not found: {commit_msg_file}\n")
        return 2

    staged_files = _get_staged_files()
    if not staged_files:
        return 0

    try:
        existing = commit_msg_file.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"prepare_commit_msg: failed to read commit message: {exc}\n")
        return 2

    if HINT_MARKER in existing:
        return 0

    # Check if all staged files are markdown (.md) docs
    docs_only = all(f.lower().endswith(".md") for f in staged_files)

    if docs_only:
        hint = (
            "\n"
            f"{HINT_MARKER}\n"
            "# ---------------------------------------------------------------------\n"
            "# Hint: This commit contains exclusively Markdown (*.md) documentation.\n"
            "# If this is a fix: or perf: commit, you can bypass the lesson citation\n"
            "# gate by adding the following trailer to the bottom of your message:\n"
            "#\n"
            "# Lesson-skip: docs-only changes\n"
            "# ---------------------------------------------------------------------\n"
        )
        try:
            with open(commit_msg_file, "a", encoding="utf-8") as f:
                f.write(hint)
        except OSError as exc:
            sys.stderr.write(f"prepare_commit_msg: failed to append hint: {exc}\n")
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
