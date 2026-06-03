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


def compile_spec(spec: EntitySpec) -> ArtefactBundle:
    """Compile an EntitySpec into an ArtefactBundle.

    Pure — does not mutate ``spec``. Deterministic — repeat calls on
    the same input produce field-equal artefacts.
    """
    prompt = synthesize_prompt(spec)
    pydantic_cls = synthesize_model(spec)
    digest = spec_hash(spec)
    return ArtefactBundle(
        prompt=prompt,
        pydantic_cls=pydantic_cls,
        tool_schema_per_backend=_EMPTY_BACKEND_SCHEMAS,
        spec_hash=digest,
        max_input_tokens=spec.max_input_tokens,
    )
