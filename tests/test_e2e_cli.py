"""End-to-end CLI tests driving the ``prompiler`` entry point as a subprocess.

Source of truth: docs/PLAN.md P7 — every CLI command exposes ``--help`` and uses
Unix exit codes (``0`` ok, ``1`` user error, ``2`` internal/path-not-found), and
``prompiler stats --since 7d`` prints a usage summary. These tests spawn the CLI
exactly as a downstream user or pre-commit hook would (``python -m prompiler.cli``),
so they exercise argument parsing, exit-code wiring, and stdout/stderr contracts
through the real process boundary rather than via in-process Typer calls.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
VALID_SPEC = EXAMPLES_DIR / "email_category.yaml"

# Every leaf command in `prompiler --help`. Each must answer `--help` with rc 0.
COMMANDS: tuple[str, ...] = (
    "validate",
    "codegen",
    "serve",
    "eval",
    "refine",
    "stats",
)


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "prompiler.cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.e2e
def test_top_level_help_exits_zero() -> None:
    proc = _run(["--help"])
    assert proc.returncode == 0
    assert "Usage:" in proc.stdout
    # Every leaf command must be advertised on the top-level help.
    for command in COMMANDS:
        assert command in proc.stdout, f"{command} missing from top-level help"


@pytest.mark.e2e
def test_version_exits_zero() -> None:
    proc = _run(["--version"])
    assert proc.returncode == 0
    assert proc.stdout.strip()


@pytest.mark.e2e
@pytest.mark.parametrize("command", COMMANDS)
def test_each_command_has_help(command: str) -> None:
    proc = _run([command, "--help"])
    assert proc.returncode == 0, f"{command} --help failed: {proc.stderr!r}"
    # Typer renders the subcommand name into the per-command Usage line; the
    # program prefix varies by invocation (`python -m ...`), so match the leaf.
    assert command in proc.stdout


@pytest.mark.e2e
def test_validate_valid_spec_exits_zero() -> None:
    proc = _run(["validate", str(VALID_SPEC)])
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"


@pytest.mark.e2e
def test_validate_examples_dir_exits_zero() -> None:
    proc = _run(["validate", str(EXAMPLES_DIR)])
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"


@pytest.mark.e2e
def test_validate_missing_path_exits_two(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    proc = _run(["validate", str(missing)])
    assert proc.returncode == 2
    assert "path not found" in proc.stderr


@pytest.mark.e2e
def test_validate_malformed_spec_exits_one(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text(":\n  this: is not a valid spec\n", encoding="utf-8")
    proc = _run(["validate", str(bad)])
    assert proc.returncode == 1
    assert proc.stderr.strip()


@pytest.mark.e2e
def test_stats_since_window_prints_summary(tmp_path: Path) -> None:
    # Empty log is a valid input: stats degrades to a zeroed window summary.
    empty_log = tmp_path / "usage.jsonl"
    empty_log.write_text("", encoding="utf-8")
    proc = _run(["stats", "--since", "7d", "--log", str(empty_log)])
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    assert "Usage over the last 7d" in proc.stdout
    assert "calls:" in proc.stdout


@pytest.mark.e2e
def test_stats_bad_window_exits_one() -> None:
    proc = _run(["stats", "--since", "not-a-window"])
    assert proc.returncode == 1
    assert proc.stderr.strip()


@pytest.mark.e2e
def test_codegen_missing_path_exits_two(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    proc = _run(["codegen", str(missing), "-o", str(tmp_path)])
    assert proc.returncode == 2
    assert "path not found" in proc.stderr
