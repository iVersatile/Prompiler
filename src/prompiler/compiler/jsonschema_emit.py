"""JSON Schema emitter (P1.8).

Renders the JSON Schema (Draft 2020-12) for an EntitySpec by way of the
synthesised Pydantic model.

Contract — architecture.md L198 (verbatim):
  "JSON Schema emitter. Pydantic v2's native ``model_json_schema()`` with
  explicit draft 2020-12 selection. Output passes a JSON-Schema-meta
  validator in unit tests."

Determinism: PRD FR-2 / PLAN.md L107 — same spec + same prompiler version =>
identical schema. Byte-level canonicalisation is the bundle stage's job
(P1.9); this layer guarantees structural (dict) determinism.
"""

from __future__ import annotations

from typing import Any

from prompiler.compiler.model import synthesize_model
from prompiler.spec import EntitySpec

DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"


def synthesize_schema(spec: EntitySpec) -> dict[str, Any]:
    """Render the JSON Schema for ``spec``.

    Pydantic v2's ``model_json_schema()`` does not inject a ``$schema`` URI on
    its own; the Draft 2020-12 URI is prepended here so downstream consumers
    can rely on dialect identification without re-parsing the model.
    """
    model = synthesize_model(spec)
    schema = model.model_json_schema()
    return {"$schema": DRAFT_2020_12_URI, **schema}
