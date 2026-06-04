#!/usr/bin/env python3
"""Pre-commit gate: refuse to commit on `main`/`master` without explicit opt-in.

Source of truth: docs/RULES.md §1.0 (feature-branch precondition).
Wired from .pre-commit-config.yaml hook id `branch-guard`.

The agent must work on a non-main feature branch before any code edit. This
gate exists because untracked files survive `git checkout` between branches,
and the pre-push working-tree-clean gate only catches *dirty* trees — not
untracked work that gets staged and committed onto the wrong branch.

Escape hatch: set `ALLOW_MAIN_COMMIT=1` in the environment for emergency
direct-to-main fixes (hotfixes, branch-protection bypasses, etc.). The escape
hatch is intentional and audited via git history.

CI exemption: when `CI=true` or `GITHUB_ACTIONS=true`, the gate no-ops with a
stderr notice. Rationale: GitHub Actions runs `pre-commit run --all-files` on
the pushed ref, which is `main` after a merge — the gate's "no commits on
main" rule is meaningless there (the commit already happened) and would
otherwise turn every post-merge CI run red.

Exit codes:
  0  on a non-main/master branch, ALLOW_MAIN_COMMIT=1, or CI env detected
  1  on main/master without ALLOW_MAIN_COMMIT=1 (local commits only)
  2  invocation error
"""

from __future__ import annotations

import os
import subprocess
import sys

_BLOCKED_BRANCHES: frozenset[str] = frozenset({"main", "master"})
_ESCAPE_HATCH_ENV: str = "ALLOW_MAIN_COMMIT"
_CI_ENV_VARS: tuple[str, ...] = ("CI", "GITHUB_ACTIONS")


def _running_in_ci() -> bool:
    return any(os.environ.get(var, "").strip().lower() == "true" for var in _CI_ENV_VARS)


def main() -> int:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        sys.stderr.write("check_branch_guard: `git` not found on PATH.\n")
        return 2

    if result.returncode != 0:
        sys.stderr.write("check_branch_guard: `git rev-parse --abbrev-ref HEAD` failed.\n")
        sys.stderr.write(result.stderr)
        return 2

    branch = result.stdout.strip()
    if branch not in _BLOCKED_BRANCHES:
        return 0

    if _running_in_ci():
        sys.stderr.write(
            f"check_branch_guard: skipping on `{branch}` because CI env detected "
            f"({', '.join(_CI_ENV_VARS)}). Local commits to main/master are still blocked.\n"
        )
        return 0

    if os.environ.get(_ESCAPE_HATCH_ENV, "").strip() == "1":
        sys.stderr.write(
            f"check_branch_guard: committing on `{branch}` because "
            f"{_ESCAPE_HATCH_ENV} is set. Proceed with caution.\n"
        )
        return 0

    sys.stderr.write(
        f"check_branch_guard: refusing to commit on `{branch}`.\n"
        f"\n"
        f"  Create a feature branch first:\n"
        f"    git checkout -b feat/<slug>\n"
        f"\n"
        f"  Or, for an audited emergency fix, set the escape hatch:\n"
        f"    {_ESCAPE_HATCH_ENV}=1 git commit ...\n"
        f"\n"
        f"Why this gate exists: RULES.md §1.0 (feature-branch precondition).\n"
        f"Untracked files survive `git checkout` between branches, so without\n"
        f"this gate work can land on `main` even when the working tree looked\n"
        f"clean at branch-switch time.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
