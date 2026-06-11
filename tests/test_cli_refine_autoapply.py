"""C1 auto-apply CLI tests: ``refine --auto-apply`` runs a bounded loop.

Exit criterion (docs/PLAN.md §2.2, C1): a >=2-iteration scripted loop runs
end-to-end; plain ``refine`` (no flag) prints-diff-only, behaviour unchanged.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prompiler.cli import main

_INVOICE_SPEC = Path(__file__).resolve().parents[1] / "examples" / "invoice.yaml"

_FIXTURES_YAML = """\
- name: acme
  input: "Invoice from Acme Corp, total 100.00"
  expected:
    vendor_name: "Acme Corp"
    total_amount: "100.00"
- name: globex
  input: "Invoice from Globex, total 250.50"
  expected:
    vendor_name: "Globex"
    total_amount: "250.50"
"""


def _write_report(path: Path) -> None:
    payload = {
        "spec": "invoice",
        "spec_hash": "deadbeef",
        "backend": "mock",
        "model": "mock",
        "aggregate": {"precision": 0.5, "recall": 0.5, "f1": 0.5},
        "per_field": {
            "vendor_name": {"p": 0.5, "r": 0.5, "f1": 0.5},
            "total_amount": {"p": 0.5, "r": 0.5, "f1": 0.5},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def report_path(tmp_path: Path) -> Path:
    path = tmp_path / "eval-report.json"
    _write_report(path)
    return path


@pytest.fixture
def prompt_path(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.txt"
    path.write_text(
        "Extract vendor_name and total_amount from the invoice.\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fixtures_path(tmp_path: Path) -> Path:
    path = tmp_path / "fixtures.yaml"
    path.write_text(_FIXTURES_YAML, encoding="utf-8")
    return path


def _make_stateful_propose():
    """Stateful stub: emits a fresh context-matching diff each call.

    A constant diff would fail on iteration 2 because ``apply_patch`` context
    no longer matches after iteration 1 rewrites the prompt's first line.
    """
    calls = {"n": 0}

    def _propose(**kwargs):
        calls["n"] += 1
        current = str(kwargs["current_prompt"])
        first_line = current.splitlines()[0]
        new_line = f"Extract every field precisely (revision {calls['n']})."
        return f"--- prompt.txt\n+++ prompt.txt\n@@ -1 +1 @@\n-{first_line}\n+{new_line}\n"

    _propose.calls = calls
    return _propose


@pytest.mark.unit
def test_auto_apply_runs_multi_iteration_loop(
    report_path: Path,
    prompt_path: Path,
    fixtures_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    propose = _make_stateful_propose()
    monkeypatch.setattr("prompiler.cli.propose_patch_sync", propose)

    rc = main(
        [
            "refine",
            str(report_path),
            str(prompt_path),
            "--auto-apply",
            "--threshold",
            "0.9",
            "--spec",
            str(_INVOICE_SPEC),
            "--fixtures",
            str(fixtures_path),
            "--backend",
            "mock",
        ]
    )

    out = capsys.readouterr().out
    assert rc == 0
    # MockAdapter returns a constant extraction -> flat f1 across rounds, so the
    # epsilon no-improvement guard halts the loop after the second round.
    assert propose.calls["n"] == 2
    assert "iteration 2" in out
    assert "revision 2" in out
    assert "stopped: stalled" in out


@pytest.mark.unit
def test_auto_apply_requires_threshold(
    report_path: Path,
    prompt_path: Path,
    fixtures_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--auto-apply`` without ``--threshold`` is a usage error (rc 2)."""
    rc = main(
        [
            "refine",
            str(report_path),
            str(prompt_path),
            "--auto-apply",
            "--spec",
            str(_INVOICE_SPEC),
            "--fixtures",
            str(fixtures_path),
            "--backend",
            "mock",
        ]
    )

    err = capsys.readouterr().err
    assert rc == 2
    assert "threshold" in err


@pytest.mark.unit
def test_plain_refine_ignores_loop_and_emits_diff(
    report_path: Path,
    prompt_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    diff = "--- prompt.txt\n+++ prompt.txt\n@@ -1 +1 @@\n-old\n+new\n"
    monkeypatch.setattr(
        "prompiler.cli.propose_patch_sync",
        lambda **kwargs: diff,
    )

    rc = main(["refine", str(report_path), str(prompt_path), "--backend", "mock"])

    out = capsys.readouterr().out
    assert rc == 0
    assert out == diff


def _git_init_commit(repo: Path) -> None:
    """Init a git repo in ``repo`` and commit everything in it.

    Inline ``-c user.*`` keeps this off the global git identity (and the
    GH007 noreply-email constraint that only matters for real pushes).
    """
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", *ident, "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", *ident, "commit", "-q", "-m", "init"], cwd=repo, check=True)


@pytest.mark.unit
def test_auto_apply_clean_tree_mutates_prompt_file(
    report_path: Path,
    prompt_path: Path,
    fixtures_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Clean-tree run writes the refined prompt back to disk (C3 exit a)."""
    propose = _make_stateful_propose()
    monkeypatch.setattr("prompiler.cli.propose_patch_sync", propose)
    _git_init_commit(tmp_path)
    before = prompt_path.read_text(encoding="utf-8")

    rc = main(
        [
            "refine",
            str(report_path),
            str(prompt_path),
            "--auto-apply",
            "--threshold",
            "0.9",
            "--spec",
            str(_INVOICE_SPEC),
            "--fixtures",
            str(fixtures_path),
            "--backend",
            "mock",
        ]
    )

    capsys.readouterr()
    after = prompt_path.read_text(encoding="utf-8")
    assert rc == 0
    assert after != before
    assert "revision" in after


@pytest.mark.unit
def test_auto_apply_dirty_tree_aborts_before_write(
    report_path: Path,
    prompt_path: Path,
    fixtures_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dirty tree without ``--force`` aborts before any write (C3 exit b)."""
    propose = _make_stateful_propose()
    monkeypatch.setattr("prompiler.cli.propose_patch_sync", propose)
    _git_init_commit(tmp_path)
    (tmp_path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    before = prompt_path.read_text(encoding="utf-8")

    rc = main(
        [
            "refine",
            str(report_path),
            str(prompt_path),
            "--auto-apply",
            "--threshold",
            "0.9",
            "--spec",
            str(_INVOICE_SPEC),
            "--fixtures",
            str(fixtures_path),
            "--backend",
            "mock",
        ]
    )

    err = capsys.readouterr().err
    assert rc == 2
    assert prompt_path.read_text(encoding="utf-8") == before
    assert propose.calls["n"] == 0
    assert "dirty" in err
    assert "--force" in err


@pytest.mark.unit
def test_auto_apply_force_overrides_dirty_refusal(
    report_path: Path,
    prompt_path: Path,
    fixtures_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--force`` overrides the dirty-tree refusal and writes (C3 exit c)."""
    propose = _make_stateful_propose()
    monkeypatch.setattr("prompiler.cli.propose_patch_sync", propose)
    _git_init_commit(tmp_path)
    (tmp_path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    before = prompt_path.read_text(encoding="utf-8")

    rc = main(
        [
            "refine",
            str(report_path),
            str(prompt_path),
            "--auto-apply",
            "--threshold",
            "0.9",
            "--spec",
            str(_INVOICE_SPEC),
            "--fixtures",
            str(fixtures_path),
            "--backend",
            "mock",
            "--force",
        ]
    )

    capsys.readouterr()
    assert rc == 0
    assert prompt_path.read_text(encoding="utf-8") != before


@pytest.mark.unit
def test_auto_apply_unverifiable_git_tree_aborts_before_write(
    report_path: Path,
    prompt_path: Path,
    fixtures_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Inside a work tree whose status can't be read, fail closed (C3 exit b).

    Regression: the guard returned "clean" on a non-zero ``git status`` exit,
    so an errored-but-real repo was treated as safe and the prompt was
    silently overwritten. The fix must refuse the write instead.
    """
    propose = _make_stateful_propose()
    monkeypatch.setattr("prompiler.cli.propose_patch_sync", propose)

    real_run = subprocess.run

    def _fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal\n")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("prompiler.cli.subprocess.run", _fake_run)
    before = prompt_path.read_text(encoding="utf-8")

    rc = main(
        [
            "refine",
            str(report_path),
            str(prompt_path),
            "--auto-apply",
            "--threshold",
            "0.9",
            "--spec",
            str(_INVOICE_SPEC),
            "--fixtures",
            str(fixtures_path),
            "--backend",
            "mock",
        ]
    )

    err = capsys.readouterr().err
    assert rc == 2
    assert prompt_path.read_text(encoding="utf-8") == before
    assert propose.calls["n"] == 0
    assert "git tree" in err
    assert "--force" in err
