#!/usr/bin/env python3
"""Regex-based secret scanner for the prompiler pre-commit + pre-push gate.

Source of truth: docs/RULES.md §8 (general constraints) and §3 (pre-push gate).
Wired from .pre-commit-config.yaml (`scan-secrets-staged`, `scan-secrets-push`).

Modes (mutually exclusive):
  --staged              Scan added lines in `git diff --cached`.
  --pre-push            Scan added lines in the push range. Reads
                        PRE_COMMIT_FROM_REF / PRE_COMMIT_TO_REF set by the
                        pre-commit framework. Falls back to the last 50
                        commits on HEAD when FROM_REF is empty or all-zeros
                        (first push of a new branch).
  --diff <range>        Scan added lines in `git diff <range>`. Used by
                        RULES.md §3 manual invocation.
  --files <path> ...    Scan whole files. Used for ad-hoc audits.

Exit codes:
  0  clean
  1  findings (printed to stderr)
  2  invocation error
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Each pattern is anchored to a vendor token shape. Keep this list short and
# precise — broad heuristics (e.g. "secret" substrings) belong in a separate
# audit tool, not in a commit-time gate.
PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic_api_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai_api_key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_pem": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
    ),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "gitlab_pat": re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
}

# Files that legitimately contain these regex literals. Allowlist is path-exact
# (not glob) to keep the policy auditable.
ALLOWLIST_PATHS: frozenset[str] = frozenset(
    {
        "scripts/scan_secrets.py",
        "docs/RULES.md",
    }
)

ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    pattern: str
    snippet: str


def _run(cmd: list[str]) -> str:
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # git diff returns 1 when there are differences with --exit-code; we
        # never pass that flag, so a non-zero return is a real error.
        sys.stderr.write(
            f"scan_secrets: `{' '.join(cmd)}` failed (rc={result.returncode}): "
            f"{result.stderr.strip()}\n"
        )
        sys.exit(2)
    return result.stdout


def _diff_added_lines(diff_text: str) -> Iterable[tuple[str, int, str]]:
    """Yield (path, new_line_number, content) for every added line in a
    unified diff. Skips deletions, context lines, and diff metadata.
    """
    path: str | None = None
    new_lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                path = None
            else:
                # `+++ b/path/to/file` — strip the `b/` prefix.
                path = target[2:] if target.startswith("b/") else target
            continue
        if raw.startswith("@@"):
            # Hunk header: @@ -old,oldc +new,newc @@
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                new_lineno = int(m.group(1))
            continue
        if path is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            yield path, new_lineno, raw[1:]
            new_lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            # Deletion — no advance of new_lineno.
            continue
        elif raw.startswith(" "):
            new_lineno += 1
        # Other lines (\, diff --git, index) are ignored.


def _scan_added_lines(
    lines: Iterable[tuple[str, int, str]],
) -> list[Finding]:
    findings: list[Finding] = []
    for path, lineno, content in lines:
        if path in ALLOWLIST_PATHS:
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                snippet = content.strip()
                if len(snippet) > 80:
                    snippet = snippet[:77] + "..."
                findings.append(
                    Finding(path=path, line=lineno, pattern=name, snippet=snippet)
                )
    return findings


def _scan_files(paths: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path_str in paths:
        if path_str in ALLOWLIST_PATHS:
            continue
        p = Path(path_str)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sys.stderr.write(f"scan_secrets: cannot read {p}: {exc}\n")
            sys.exit(2)
        for lineno, content in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                if pattern.search(content):
                    snippet = content.strip()
                    if len(snippet) > 80:
                        snippet = snippet[:77] + "..."
                    findings.append(
                        Finding(
                            path=path_str,
                            line=lineno,
                            pattern=name,
                            snippet=snippet,
                        )
                    )
    return findings


def _report(findings: list[Finding]) -> None:
    sys.stderr.write(
        f"scan_secrets: {len(findings)} potential secret(s) detected:\n"
    )
    for f in findings:
        sys.stderr.write(
            f"  {f.path}:{f.line}  [{f.pattern}]  {f.snippet}\n"
        )
    sys.stderr.write(
        "\nRemediation:\n"
        "  1. Remove the secret from the change.\n"
        "  2. Rotate the credential at the vendor.\n"
        "  3. If the match is a false positive, scope an allowlist entry in\n"
        "     scripts/scan_secrets.py:ALLOWLIST_PATHS and justify in the commit.\n"
    )


def _resolve_push_range() -> str:
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF", "")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF", "HEAD")
    if not from_ref or from_ref == ZERO_SHA:
        # New-branch push: pre-commit cannot determine an upstream. Inspect the
        # last 50 commits on HEAD as a conservative bound.
        return ""
    return f"{from_ref}..{to_ref}"


def _diff_for_range(diff_range: str) -> str:
    if diff_range:
        return _run(["git", "diff", "-U0", diff_range])
    return _run(["git", "log", "-p", "-U0", "-n", "50", "HEAD"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_secrets",
        description="Regex-based secret scanner for prompiler.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="Scan staged diff.")
    mode.add_argument(
        "--pre-push",
        action="store_true",
        help="Scan the push range from PRE_COMMIT_FROM_REF/TO_REF.",
    )
    mode.add_argument(
        "--diff",
        metavar="RANGE",
        help="Scan an explicit git diff range, e.g. main..HEAD.",
    )
    mode.add_argument(
        "--files",
        nargs="+",
        metavar="PATH",
        help="Scan one or more files in full.",
    )
    args = parser.parse_args(argv)

    if args.staged:
        diff_text = _run(["git", "diff", "--cached", "-U0"])
        findings = _scan_added_lines(_diff_added_lines(diff_text))
    elif args.pre_push:
        diff_text = _diff_for_range(_resolve_push_range())
        findings = _scan_added_lines(_diff_added_lines(diff_text))
    elif args.diff:
        diff_text = _run(["git", "diff", "-U0", args.diff])
        findings = _scan_added_lines(_diff_added_lines(diff_text))
    else:
        findings = _scan_files(args.files)

    if findings:
        _report(findings)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
