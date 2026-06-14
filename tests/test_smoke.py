"""Smoke tests asserting the package is importable and exposes __version__."""

import re
from pathlib import Path

import pytest

import prompiler


@pytest.mark.unit
def test_package_has_version() -> None:
    assert isinstance(prompiler.__version__, str)
    assert prompiler.__version__ != ""


@pytest.mark.unit
def test_version_is_pep440_like() -> None:
    parts = prompiler.__version__.split(".")
    assert len(parts) >= 2
    assert parts[0].isdigit()


@pytest.mark.unit
def test_version_derived_from_metadata() -> None:
    """A1: __version__ is read from installed package metadata, not a frozen
    literal hand-bumped in lockstep with pyproject (retires the Q5 K1 lockstep).
    pyproject.toml is the single source of truth for the release version."""
    init_src = (
        Path(__file__).resolve().parents[1] / "src" / "prompiler" / "__init__.py"
    ).read_text(encoding="utf-8")
    # Derivation from installed metadata must be present.
    assert "from importlib.metadata import" in init_src
    assert 'version("prompiler")' in init_src
    # The release version must not be re-declared as a module-level literal;
    # pyproject.toml is the sole place it lives. (A sentinel inside the
    # PackageNotFoundError branch is indented, so this line-anchored check
    # ignores it.)
    assert not re.search(r'^__version__\s*=\s*["\']\d', init_src, re.MULTILINE)
