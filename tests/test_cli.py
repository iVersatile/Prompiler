"""Unit tests for prompiler.cli (P0 stub).

Covers:
- ``--version`` prints the package version and exits 0.
- ``validate <existing path>`` returns 0.
- ``validate <missing path>`` returns 2 with a stderr diagnostic.
- ``main()`` with no subcommand prints help and returns 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prompiler import __version__
from prompiler.cli import main


@pytest.mark.unit
def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


@pytest.mark.unit
def test_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "usage:" in out.lower()


@pytest.mark.unit
def test_validate_existing_path(tmp_path: Path) -> None:
    target = tmp_path / "prompts"
    target.mkdir()
    rc = main(["validate", str(target)])
    assert rc == 0


@pytest.mark.unit
def test_validate_missing_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "does-not-exist"
    rc = main(["validate", str(missing)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "path not found" in err
    assert str(missing) in err
