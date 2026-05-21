#!/usr/bin/env python3
"""prepare-commit-msg hook: auto-suggest Lesson-skip for docs-only diffs.

Invoked by the pre-commit framework on the ``prepare-commit-msg`` git stage.
When the staged diff touches only ``*.md`` files, append a hint block to the
commit-message buffer pointing the user at the bare ``Lesson-skip:`` trailer
accepted by ``scripts/check_lesson_cite.py`` for docs-only scopes (docs/RULES.md §4).

Callers:
- ``.pre-commit-config.yaml`` ``prepare-commit-msg-docs-hint`` hook (stage:
  ``prepare-commit-msg``), which the ``pre-commit`` framework wires into
  ``.git/hooks/prepare-commit-msg`` after ``pre-commit install --hook-type
  prepare-commit-msg``.

Inputs:
- ``argv[1]``: path to the commit-message buffer file managed by git.
- ``git diff --cached --name-only``: staged path list (one path per line,
  no date or structured fields).

Outputs:
- Appends a ``# prompiler: docs-only diff detected`` hint block to the
  commit-message buffer when the staged diff is ``*.md``-only and the
  marker is not already present. UTF-8 text; no structured fields.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HINT_MARKER = "# prompiler: docs-only diff detected"
HINT_BLOCK = (
    f"{HINT_MARKER}\n"
    "# The LL-citation gate (docs/RULES.md §4) will accept a bare\n"
    "# `Lesson-skip:` trailer for this commit — no reason required.\n"
    "# Example trailer:\n"
    "#\n"
    "#     Lesson-skip:\n"
)


def _staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _is_docs_only(paths: list[str]) -> bool:
    if not paths:
        return False
    return all(p.lower().endswith(".md") for p in paths)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    msg_path = Path(argv[1])
    try:
        existing = msg_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    if HINT_MARKER in existing:
        return 0
    if not _is_docs_only(_staged_paths()):
        return 0
    msg_path.write_text(existing + "\n" + HINT_BLOCK, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
