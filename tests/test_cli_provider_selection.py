"""D3 — provider (backend) selection precedence for the CLI.

The active backend resolves through the standard precedence chain (LL-008):
explicit kwarg → ``PROMPILER_BACKEND`` env var → ``[tool.prompiler] backend``
in ``pyproject.toml`` → hardcoded default. ``None`` marks an unsupplied kwarg
(no backend is named "none", so the sentinel is unambiguous).
"""

from __future__ import annotations

import pytest
import typer

from prompiler.cli import _Backend, _resolve_backend

_ENV = "PROMPILER_BACKEND"


@pytest.mark.unit
def test_kwarg_beats_env_beats_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "ollama")
    block = {"backend": "ollama"}
    assert _resolve_backend(_Backend.mock, block) == "mock"
    assert _resolve_backend(None, block) == "ollama"
    monkeypatch.setenv(_ENV, "mock")
    assert _resolve_backend(None, {"backend": "ollama"}) == "mock"


@pytest.mark.unit
def test_pyproject_beats_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert _resolve_backend(None, {"backend": "mock"}) == "mock"


@pytest.mark.unit
def test_default_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert _resolve_backend(None, {}) == "ollama"


@pytest.mark.unit
def test_unknown_env_backend_is_bad_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "gpt9000")
    with pytest.raises(typer.BadParameter):
        _resolve_backend(None, {})


@pytest.mark.unit
def test_unknown_pyproject_backend_is_bad_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    with pytest.raises(typer.BadParameter):
        _resolve_backend(None, {"backend": "gpt9000"})
