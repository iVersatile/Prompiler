#!/usr/bin/env python3
"""Pre-push gate: refuse to push if the working tree has uncommitted changes.

Source of truth: docs/RULES.md §3 (pre-push gate, working-tree-clean check).
Wired from .pre-commit-config.yaml hook id `working-tree-clean`.

A clean tree at push time prevents the common "passes locally, fails CI"
failure mode where the developer commits a subset of changes and the staged
slice does not match what was tested locally.

Exit codes:
  0  working tree clean
  1  uncommitted changes present (paths listed to stderr)
  2  invocation error
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        sys.stderr.write("check_clean_tree: `git` not found on PATH.\n")
        return 2

    if result.returncode != 0:
        sys.stderr.write(
            "check_clean_tree: `git status --porcelain` failed "
            f"(rc={result.returncode}): {result.stderr.strip()}\n"
        )
        return 2

    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    if not dirty:
        return 0

    sys.stderr.write(
        "check_clean_tree: working tree is not clean. Commit or stash "
        "before pushing:\n"
    )
    for line in dirty:
        sys.stderr.write(f"  {line}\n")
    sys.stderr.write(
        "\nWhy this gate exists: RULES.md §3 requires a clean tree at push "
        "time so the commit being pushed matches what the local pre-commit "
        "gate proved green.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
