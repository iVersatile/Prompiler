"""Runtime wiring for the MCP server (P6 — PLAN.md L244-254).

Two pure helpers keep ``__main__`` thin and unit-testable:

- ``build_backend``  — env-selected single backend (option A) plus a shared
  ``CapturingHook`` so the server can surface per-call token usage in tool
  ``_meta``. Cloud adapters resolve credentials from the environment via
  ``EnvVarProvider``; Ollama needs none.
- ``load_examples``  — compile every spec YAML under ``examples_dir`` into the
  registry and capture its raw source for the ``prompiler://specs`` resource.
"""

from __future__ import annotations

from pathlib import Path

from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.credentials import EnvVarProvider
from prompiler.backends.gemini import GeminiAdapter
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter
from prompiler.compiler import compile_spec
from prompiler.eval.runner import CapturingHook
from prompiler.runtime.registry import Registry
from prompiler.spec import load_spec

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


def build_backend(name: str, *, base_url: str | None = None) -> tuple[object, CapturingHook]:
    """Construct the backend adapter selected by ``name`` plus its usage hook."""
    hook = CapturingHook()
    creds = EnvVarProvider()
    if name == "claude":
        return ClaudeAdapter(credentials=creds, observability=hook), hook
    if name == "openai":
        return OpenAIAdapter(credentials=creds, observability=hook), hook
    if name == "gemini":
        return GeminiAdapter(credentials=creds, observability=hook), hook
    if name == "ollama":
        if base_url is not None:
            return OllamaAdapter(observability=hook, base_url=base_url), hook
        return OllamaAdapter(observability=hook), hook
    raise ValueError(f"unsupported backend {name!r}: expected claude|openai|gemini|ollama")


def load_examples(
    examples_dir: Path | str, *, registry: Registry | None = None
) -> tuple[Registry, dict[str, str]]:
    """Compile every spec YAML under ``examples_dir`` into ``registry``."""
    registry = registry if registry is not None else Registry()
    sources: dict[str, str] = {}
    for path in sorted(
        p for p in Path(examples_dir).iterdir() if p.is_file() and p.suffix in _YAML_SUFFIXES
    ):
        spec = load_spec(path)
        bundle = compile_spec(spec)
        registry.register(spec.name, bundle)
        sources[spec.name] = path.read_text(encoding="utf-8")
    return registry, sources
