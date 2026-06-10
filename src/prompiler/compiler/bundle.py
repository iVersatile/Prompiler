"""ArtefactBundle + compile entry point (P1.9).

Public entry: ``compile_spec(spec) -> ArtefactBundle``.

Bundle shape (architecture.md L132-134):
``ArtefactBundle{prompt, pydantic_cls, tool_schema_per_backend, spec_hash}``.

Determinism (architecture.md L168): two calls on the same spec yield
field-equal artefacts. Class identity for ``pydantic_cls`` is NOT a
contract — ``pydantic.create_model`` returns a fresh class each call;
the determinism guarantee is on its JSON Schema.

Adapter projection (``tool_schema_per_backend``) lands in P2; for now
the bundle exposes an empty read-only mapping so downstream code can
already key off backend names.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from prompiler.compiler.model import synthesize_model
from prompiler.compiler.prompt_synth import synthesize_prompt
from prompiler.spec import EntitySpec, spec_hash

_EMPTY_BACKEND_SCHEMAS: MappingProxyType[str, dict[str, Any]] = MappingProxyType({})


@dataclass(frozen=True)
class ArtefactBundle:
    """Compiled artefacts for a single spec.

    Frozen dataclass — fields are immutable. ``tool_schema_per_backend``
    is a ``MappingProxyType`` so the mapping itself rejects mutation.

    ``max_input_tokens`` carries the spec's doc-size guardrail
    (architecture.md L267) into the run-time layer. The orchestrator
    enforces the cap before dispatching to the adapter; ``None`` disables
    the check.
    """

    prompt: str
    pydantic_cls: type[BaseModel]
    tool_schema_per_backend: MappingProxyType[str, dict[str, Any]]
    spec_hash: str
    max_input_tokens: int | None = None


@dataclass(frozen=True)
class CompileCacheInfo:
    """Observable counters for the compile-side memoization cache."""

    hits: int
    misses: int
    currsize: int


_COMPILE_CACHE: dict[str, ArtefactBundle] = {}
_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0}


def compile_spec(spec: EntitySpec) -> ArtefactBundle:
    """Compile an EntitySpec into an ArtefactBundle.

    Pure — does not mutate ``spec``. Deterministic — repeat calls on
    the same input produce field-equal artefacts.

    Memoized on ``spec_hash``: a second call on a field-equal spec is a
    cache hit returning the same bundle, which is strictly stronger than
    the field-equal determinism contract. Observe via
    :func:`compile_cache_info`; reset via :func:`compile_cache_clear`.
    """
    digest = spec_hash(spec)
    cached = _COMPILE_CACHE.get(digest)
    if cached is not None:
        _CACHE_STATS["hits"] += 1
        return cached
    _CACHE_STATS["misses"] += 1
    bundle = ArtefactBundle(
        prompt=synthesize_prompt(spec),
        pydantic_cls=synthesize_model(spec),
        tool_schema_per_backend=_EMPTY_BACKEND_SCHEMAS,
        spec_hash=digest,
        max_input_tokens=spec.max_input_tokens,
    )
    _COMPILE_CACHE[digest] = bundle
    return bundle


def compile_cache_info() -> CompileCacheInfo:
    """Return current cache hit/miss counts and resident entry count."""
    return CompileCacheInfo(
        hits=_CACHE_STATS["hits"],
        misses=_CACHE_STATS["misses"],
        currsize=len(_COMPILE_CACHE),
    )


def compile_cache_clear() -> None:
    """Empty the cache and zero the hit/miss counters (test isolation)."""
    _COMPILE_CACHE.clear()
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["misses"] = 0
