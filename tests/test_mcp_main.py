"""Tests for prompiler.mcp.__main__ (P0 env helpers + P6 CLI wiring).

Covers env parsing helpers (``_env_bool``, ``_env_int``) carried over from P0,
plus the P6 transport/backend/examples resolution helpers and the ``main()``
entry point that wires a FastMCP server over stdio (default) or HTTP.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from prompiler.mcp import __main__ as mcp_main
from prompiler.mcp.server import HEALTHZ_PATH


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
def test_parse_args_defaults() -> None:
    args = mcp_main._parse_args([])
    assert args.transport == "stdio"
    assert args.port is None


@pytest.mark.unit
def test_parse_args_http_with_port() -> None:
    args = mcp_main._parse_args(["--transport", "http", "--port", "9000"])
    assert args.transport == "http"
    assert args.port == 9000


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cli", "expected"),
    [("stdio", "stdio"), ("http", "streamable-http")],
)
def test_resolve_transport(cli: str, expected: str) -> None:
    assert mcp_main._resolve_transport(cli) == expected


@pytest.mark.unit
def test_resolve_backend_name_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_MCP_BACKEND", raising=False)
    assert mcp_main._resolve_backend_name() == "ollama"


@pytest.mark.unit
def test_resolve_backend_name_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_MCP_BACKEND", "claude")
    assert mcp_main._resolve_backend_name() == "claude"


@pytest.mark.unit
def test_resolve_examples_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_MCP_EXAMPLES_DIR", raising=False)
    resolved = mcp_main._resolve_examples_dir()
    assert resolved.name == "examples"
    assert resolved.is_dir()


@pytest.mark.unit
def test_resolve_examples_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROMPILER_MCP_EXAMPLES_DIR", str(tmp_path))
    assert mcp_main._resolve_examples_dir() == tmp_path


@pytest.mark.unit
def test_serve_mcp_stdio_runs_default(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = FastMCP("test")
    calls: list[str] = []
    monkeypatch.setattr(mcp, "run", lambda transport: calls.append(transport))
    mcp_main._serve_mcp(
        mcp, transport="stdio", host="127.0.0.1", port=8765, allow_non_loopback=False
    )
    assert calls == ["stdio"]
    assert HEALTHZ_PATH not in {r.path for r in mcp._custom_starlette_routes}


@pytest.mark.unit
def test_serve_mcp_http_sets_settings_and_mounts_healthz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = FastMCP("test")
    calls: list[str] = []
    monkeypatch.setattr(mcp, "run", lambda transport: calls.append(transport))
    mcp_main._serve_mcp(
        mcp,
        transport="streamable-http",
        host="127.0.0.1",
        port=9999,
        allow_non_loopback=False,
    )
    assert calls == ["streamable-http"]
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 9999
    assert HEALTHZ_PATH in {r.path for r in mcp._custom_starlette_routes}


@pytest.mark.unit
def test_serve_mcp_http_rejects_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = FastMCP("test")
    calls: list[str] = []
    monkeypatch.setattr(mcp, "run", lambda transport: calls.append(transport))
    with pytest.raises(ValueError, match="non-loopback"):
        mcp_main._serve_mcp(
            mcp,
            transport="streamable-http",
            host="0.0.0.0",
            port=9999,
            allow_non_loopback=False,
        )
    assert calls == []


@pytest.mark.unit
def test_serve_mcp_http_allows_non_loopback_with_optin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = FastMCP("test")
    calls: list[str] = []
    monkeypatch.setattr(mcp, "run", lambda transport: calls.append(transport))
    mcp_main._serve_mcp(
        mcp,
        transport="streamable-http",
        host="0.0.0.0",
        port=9999,
        allow_non_loopback=True,
    )
    assert calls == ["streamable-http"]
    assert mcp.settings.host == "0.0.0.0"


@pytest.mark.unit
def test_main_wires_http_transport_and_cli_port_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMPILER_MCP_PORT", "8765")
    monkeypatch.delenv("PROMPILER_MCP_HOST", raising=False)
    monkeypatch.delenv("PROMPILER_MCP_ALLOW_NON_LOOPBACK", raising=False)
    captured: dict[str, object] = {}

    def fake_build_backend(name: str) -> tuple[str, object]:
        return f"backend:{name}", object()

    def fake_load_examples(examples_dir: object) -> tuple[object, dict[str, str]]:
        return object(), {}

    def fake_build_mcp(registry: object, backend: object, **kwargs: object) -> str:
        captured["backend"] = backend
        return "mcp-instance"

    def fake_serve_mcp(
        mcp: object,
        *,
        transport: str,
        host: str,
        port: int,
        allow_non_loopback: bool,
    ) -> None:
        captured["transport"] = transport
        captured["port"] = port

    monkeypatch.setattr(mcp_main, "build_backend", fake_build_backend)
    monkeypatch.setattr(mcp_main, "load_examples", fake_load_examples)
    monkeypatch.setattr(mcp_main, "build_mcp", fake_build_mcp)
    monkeypatch.setattr(mcp_main, "_serve_mcp", fake_serve_mcp)

    rc = mcp_main.main(["--transport", "http", "--port", "9000"])
    assert rc == 0
    assert captured["backend"] == "backend:ollama"
    assert captured["transport"] == "streamable-http"
    assert captured["port"] == 9000


@pytest.mark.unit
def test_main_returns_zero_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_MCP_HOST", raising=False)
    monkeypatch.delenv("PROMPILER_MCP_PORT", raising=False)
    monkeypatch.delenv("PROMPILER_MCP_ALLOW_NON_LOOPBACK", raising=False)

    def fake_build_backend(name: str) -> tuple[str, object]:
        return "backend", object()

    def fake_load_examples(examples_dir: object) -> tuple[object, dict[str, str]]:
        return object(), {}

    def fake_build_mcp(registry: object, backend: object, **kwargs: object) -> str:
        return "mcp-instance"

    def raising_serve_mcp(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_main, "build_backend", fake_build_backend)
    monkeypatch.setattr(mcp_main, "load_examples", fake_load_examples)
    monkeypatch.setattr(mcp_main, "build_mcp", fake_build_mcp)
    monkeypatch.setattr(mcp_main, "_serve_mcp", raising_serve_mcp)

    assert mcp_main.main([]) == 0
