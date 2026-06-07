"""Unit tests for MCP runtime wiring (P6 — PLAN.md L244-254)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.gemini import GeminiAdapter
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter
from prompiler.eval.runner import CapturingHook
from prompiler.mcp.runtime import build_backend, load_examples
from prompiler.runtime.registry import Registry

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def _creds_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "adapter_cls"),
    [("claude", ClaudeAdapter), ("openai", OpenAIAdapter), ("gemini", GeminiAdapter)],
)
def test_build_backend_cloud_uses_env_credentials(
    name: str, adapter_cls: type, _creds_env: None
) -> None:
    backend, hook = build_backend(name)
    assert isinstance(backend, adapter_cls)
    assert isinstance(hook, CapturingHook)


@pytest.mark.unit
def test_build_backend_ollama_no_credentials() -> None:
    backend, hook = build_backend("ollama")
    assert isinstance(backend, OllamaAdapter)
    assert isinstance(hook, CapturingHook)


@pytest.mark.unit
def test_build_backend_ollama_honors_base_url() -> None:
    backend, _ = build_backend("ollama", base_url="http://example.test:11434")
    assert isinstance(backend, OllamaAdapter)


@pytest.mark.unit
def test_build_backend_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported backend"):
        build_backend("mistral")


@pytest.mark.unit
def test_load_examples_registers_every_spec() -> None:
    registry, sources = load_examples(_EXAMPLES_DIR)
    assert isinstance(registry, Registry)
    assert set(registry.names()) == set(sources)
    assert "invoice" in registry
    assert "spec_version" in sources["invoice"]


@pytest.mark.unit
def test_load_examples_sources_are_raw_yaml() -> None:
    _, sources = load_examples(_EXAMPLES_DIR)
    raw = (_EXAMPLES_DIR / "invoice.yaml").read_text(encoding="utf-8")
    assert sources["invoice"] == raw


@pytest.mark.unit
def test_load_examples_uses_supplied_registry() -> None:
    registry = Registry()
    returned, _ = load_examples(_EXAMPLES_DIR, registry=registry)
    assert returned is registry


@pytest.mark.unit
def test_load_examples_empty_dir(tmp_path: Path) -> None:
    registry, sources = load_examples(tmp_path)
    assert registry.names() == frozenset()
    assert sources == {}
