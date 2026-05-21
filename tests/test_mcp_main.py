"""Tests for prompiler.mcp.__main__ (P0 L63).

Covers env parsing helpers (``_env_bool``, ``_env_int``) and the ``main()``
entry point that wires env vars to :func:`prompiler.mcp.server.serve`.
"""

from __future__ import annotations

import pytest

from prompiler.mcp import __main__ as mcp_main


@pytest.mark.unit
def test_env_bool_missing_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_TEST_FLAG", raising=False)
    assert mcp_main._env_bool("PROMPILER_TEST_FLAG") is False
    assert mcp_main._env_bool("PROMPILER_TEST_FLAG", default=True) is True


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on", "  on  "])
def test_env_bool_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("PROMPILER_TEST_FLAG", raw)
    assert mcp_main._env_bool("PROMPILER_TEST_FLAG") is True


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "maybe"])
def test_env_bool_non_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("PROMPILER_TEST_FLAG", raw)
    assert mcp_main._env_bool("PROMPILER_TEST_FLAG") is False


@pytest.mark.unit
def test_env_int_missing_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_TEST_INT", raising=False)
    assert mcp_main._env_int("PROMPILER_TEST_INT", default=42) == 42


@pytest.mark.unit
def test_env_int_empty_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_TEST_INT", "   ")
    assert mcp_main._env_int("PROMPILER_TEST_INT", default=7) == 7


@pytest.mark.unit
def test_env_int_parses_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_TEST_INT", "9001")
    assert mcp_main._env_int("PROMPILER_TEST_INT", default=0) == 9001


@pytest.mark.unit
def test_env_int_invalid_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PROMPILER_TEST_INT", "not-an-int")
    with pytest.raises(SystemExit) as exc_info:
        mcp_main._env_int("PROMPILER_TEST_INT", default=0)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "PROMPILER_TEST_INT" in err
    assert "not-an-int" in err


@pytest.mark.unit
def test_main_invokes_serve_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("PROMPILER_MCP_PORT", "1234")
    monkeypatch.setenv("PROMPILER_MCP_ALLOW_NON_LOOPBACK", "1")
    calls: dict[str, object] = {}

    def fake_serve(*, host: str, port: int, allow_non_loopback: bool) -> None:
        calls["host"] = host
        calls["port"] = port
        calls["allow_non_loopback"] = allow_non_loopback

    monkeypatch.setattr(mcp_main, "serve", fake_serve)
    assert mcp_main.main() == 0
    assert calls == {"host": "0.0.0.0", "port": 1234, "allow_non_loopback": True}


@pytest.mark.unit
def test_main_returns_zero_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_MCP_HOST", raising=False)
    monkeypatch.delenv("PROMPILER_MCP_PORT", raising=False)
    monkeypatch.delenv("PROMPILER_MCP_ALLOW_NON_LOOPBACK", raising=False)

    def raising_serve(**_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_main, "serve", raising_serve)
    assert mcp_main.main() == 0
