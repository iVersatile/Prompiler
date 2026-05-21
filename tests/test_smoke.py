"""Smoke tests asserting the package is importable and exposes __version__."""

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
