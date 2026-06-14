"""Smoke tests asserting the package is importable and exposes __version__."""

import re
import tomllib
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


@pytest.mark.unit
def test_compiler_protocol_version_independent_of_version() -> None:
    """A2 (RULES §10): COMPILER_PROTOCOL_VERSION must not be derived from, or
    equal-by-construction to, __version__. The protocol version gates spec_hash
    and bumps only on AST / projection / serialisation changes — bumping the
    package release version must never move it."""
    # Value independence: the two symbols must not be equal-by-construction.
    assert prompiler.__version__ != prompiler.COMPILER_PROTOCOL_VERSION
    # Source independence: the protocol version is a standalone string literal,
    # not derived from __version__ or the package metadata.
    init_src = (
        Path(__file__).resolve().parents[1] / "src" / "prompiler" / "__init__.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"^COMPILER_PROTOCOL_VERSION\s*=\s*(.+)$", init_src, re.MULTILINE)
    assert match is not None
    rhs = match.group(1).strip()
    assert re.fullmatch(r"""["'][^"']*["']""", rhs), (
        f"COMPILER_PROTOCOL_VERSION must be a standalone string literal, got: {rhs!r}"
    )


@pytest.mark.unit
def test_pyproject_version_matches_package_version() -> None:
    """B1: pyproject.toml is the single version literal; __version__ derives from
    installed metadata. They can silently diverge when pyproject is bumped but the
    package is not reinstalled (`uv sync`). This guard turns that stale-install lag
    into a loud failure instead of letting the running version drift from source."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    declared = data["project"]["version"]
    assert declared == prompiler.__version__, (
        f"pyproject [project].version is {declared!r} but the installed package "
        f"reports {prompiler.__version__!r}; run `uv sync` to reinstall."
    )
