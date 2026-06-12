"""Unit tests for prompiler.compile — entry point + ArtefactBundle (P1.9).

Covers PLAN.md L100 scope (verbatim):
  "prompiler.compile() entry point."

Contract anchored in architecture.md L132-134 (verbatim):
  "registry.get(\"invoice\") -- ArtefactBundle{prompt, pydantic_cls,
  tool_schema_per_backend, spec_hash}"

Determinism contract (architecture.md L168, verbatim):
  "Inputs to ``compile``: spec YAML + ``prompiler`` version => byte-identical
  artefacts. Verified by the local test gate (``docs/RULES.md`` §9.3): each
  example spec is compiled twice and the two artefacts must ``diff -q`` clean."

Bundle frozen at compile time (architecture.md L162, verbatim):
  "tool schemas are dataclasses frozen at compile time"

Perf budget (PRD.md L375): compile < 200 ms per spec.

Coverage gate (PLAN.md L113): >= 90% on ``compiler/`` package.

Public surface (architecture.md L116, verbatim):
  "from prompiler import compile, run, run_sync, run_batch, eval, refine"

The top-level ``compile`` deliberately shadows the Python builtin; this is the
documented public API and tests assert it as such.
"""

from __future__ import annotations

import dataclasses
import time
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import prompiler
from prompiler import ArtefactBundle, compile
from prompiler.compiler import (
    compile_cache_clear,
    compile_cache_info,
    compile_spec,
    synthesize_prompt,
    synthesize_schema,
)
from prompiler.spec import EntitySpec, spec_hash


def _load(data: dict[str, Any]) -> EntitySpec:
    return EntitySpec.model_validate(data)


def _extract_spec(**overrides: Any) -> EntitySpec:
    base: dict[str, Any] = {
        "spec_version": 2,
        "name": "invoice",
        "task": "extract",
        "description": "Extract invoice fields from a scanned receipt.",
        "fields": [
            {
                "name": "invoice_number",
                "type": "string",
                "required": True,
                "pattern": r"^[A-Z0-9-]{3,32}$",
            },
            {
                "name": "total_amount",
                "type": "decimal",
                "required": True,
                "minimum": 0,
            },
            {
                "name": "currency",
                "type": "enum",
                "required": True,
                "values": ["USD", "EUR"],
            },
            {
                "name": "notes",
                "type": "string",
                "required": False,
            },
            {
                "name": "billing",
                "type": "object",
                "required": True,
                "fields": [
                    {"name": "city", "type": "string", "required": True},
                    {"name": "zip", "type": "string", "required": True},
                ],
            },
        ],
    }
    base.update(overrides)
    return _load(base)


def _classify_spec(**overrides: Any) -> EntitySpec:
    base: dict[str, Any] = {
        "spec_version": 2,
        "name": "email_category",
        "task": "classify",
        "description": "Classify customer email by intent.",
        "labels": [
            {"name": "billing", "description": "Billing or payment questions."},
            {"name": "support", "description": "Technical support requests."},
            {"name": "other", "description": "Anything else."},
        ],
    }
    base.update(overrides)
    return _load(base)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compile_exported_from_top_level() -> None:
    assert hasattr(prompiler, "compile")
    assert callable(prompiler.compile)


@pytest.mark.unit
def test_artefact_bundle_exported_from_top_level() -> None:
    assert hasattr(prompiler, "ArtefactBundle")


@pytest.mark.unit
def test_compile_spec_exported_from_compiler_submodule() -> None:
    assert callable(compile_spec)


@pytest.mark.unit
def test_top_level_compile_aliases_compile_spec() -> None:
    """``prompiler.compile`` and ``prompiler.compiler.compile_spec`` must
    refer to the same callable so that downstream consumers cannot drift."""
    assert compile is compile_spec


# ---------------------------------------------------------------------------
# Return type + bundle shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compile_returns_artefact_bundle_instance() -> None:
    bundle = compile(_extract_spec())
    assert isinstance(bundle, ArtefactBundle)


@pytest.mark.unit
def test_artefact_bundle_has_documented_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(ArtefactBundle)}
    assert field_names == {
        "prompt",
        "pydantic_cls",
        "tool_schema_per_backend",
        "spec_hash",
        "max_input_tokens",
    }


@pytest.mark.unit
def test_compile_threads_max_input_tokens_into_bundle() -> None:
    from prompiler.spec import EntitySpec, FieldSpec

    spec = EntitySpec(
        spec_version=2,
        name="capped",
        task="extract",
        fields=[FieldSpec(name="x", type="string")],
        max_input_tokens=4096,
    )
    bundle = compile(spec)
    assert bundle.max_input_tokens == 4096


@pytest.mark.unit
def test_compile_defaults_max_input_tokens_to_none() -> None:
    bundle = compile(_extract_spec())
    assert bundle.max_input_tokens is None


@pytest.mark.unit
def test_artefact_bundle_is_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(ArtefactBundle)
    bundle = compile(_extract_spec())
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.prompt = "tampered"  # type: ignore[misc]


@pytest.mark.unit
def test_artefact_bundle_pydantic_cls_field_frozen() -> None:
    bundle = compile(_extract_spec())
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.pydantic_cls = BaseModel  # type: ignore[misc]


@pytest.mark.unit
def test_artefact_bundle_tool_schema_field_frozen() -> None:
    bundle = compile(_extract_spec())
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.tool_schema_per_backend = {}  # type: ignore[assignment,misc]


@pytest.mark.unit
def test_artefact_bundle_spec_hash_field_frozen() -> None:
    bundle = compile(_extract_spec())
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.spec_hash = "0" * 64  # type: ignore[misc]


# ---------------------------------------------------------------------------
# prompt field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prompt_is_string() -> None:
    bundle = compile(_extract_spec())
    assert isinstance(bundle.prompt, str)


@pytest.mark.unit
def test_prompt_matches_synthesize_prompt() -> None:
    spec = _extract_spec()
    bundle = compile(spec)
    assert bundle.prompt == synthesize_prompt(spec)


@pytest.mark.unit
def test_prompt_non_empty() -> None:
    bundle = compile(_extract_spec())
    assert bundle.prompt.strip() != ""


# ---------------------------------------------------------------------------
# pydantic_cls field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pydantic_cls_is_basemodel_subclass() -> None:
    bundle = compile(_extract_spec())
    assert isinstance(bundle.pydantic_cls, type)
    assert issubclass(bundle.pydantic_cls, BaseModel)


@pytest.mark.unit
def test_pydantic_cls_validates_correct_payload() -> None:
    bundle = compile(_extract_spec())
    instance = bundle.pydantic_cls.model_validate(
        {
            "invoice_number": "INV-001",
            "total_amount": "12.34",
            "currency": "USD",
            "notes": None,
            "billing": {"city": "Berlin", "zip": "10115"},
        }
    )
    assert instance is not None


@pytest.mark.unit
def test_pydantic_cls_rejects_invalid_payload() -> None:
    bundle = compile(_extract_spec())
    with pytest.raises(ValidationError):
        bundle.pydantic_cls.model_validate(
            {
                "invoice_number": "bad lowercase",
                "total_amount": "12.34",
                "currency": "USD",
                "billing": {"city": "Berlin", "zip": "10115"},
            }
        )


@pytest.mark.unit
def test_pydantic_cls_schema_matches_jsonschema_emitter() -> None:
    """The class carried in the bundle, when asked for its JSON Schema with
    the Draft 2020-12 URI prepended, must equal the standalone emitter's
    output. This is the seam between P1.5/6 (model), P1.8 (schema), and P1.9
    (bundle)."""
    spec = _extract_spec()
    bundle = compile(spec)
    DRAFT_URI = "https://json-schema.org/draft/2020-12/schema"
    raw = bundle.pydantic_cls.model_json_schema()
    assert {"$schema": DRAFT_URI, **raw} == synthesize_schema(spec)


# ---------------------------------------------------------------------------
# tool_schema_per_backend field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_schema_per_backend_is_mapping() -> None:
    bundle = compile(_extract_spec())
    # MappingProxyType supports dict(...) and __getitem__; assert it behaves
    # as a read-only mapping.
    assert isinstance(dict(bundle.tool_schema_per_backend), dict)


@pytest.mark.unit
def test_tool_schema_per_backend_empty_until_p2_adapters() -> None:
    """P2 introduces adapters; in P1 the bundle ships with no backend
    projections. The interim shape is an empty read-only mapping."""
    bundle = compile(_extract_spec())
    assert dict(bundle.tool_schema_per_backend) == {}


@pytest.mark.unit
def test_tool_schema_per_backend_rejects_mutation() -> None:
    """``MappingProxyType`` is the canonical read-only mapping; assigning
    into it must raise ``TypeError``."""
    bundle = compile(_extract_spec())
    assert isinstance(bundle.tool_schema_per_backend, MappingProxyType)
    with pytest.raises(TypeError):
        bundle.tool_schema_per_backend["claude"] = {}  # type: ignore[index]


# ---------------------------------------------------------------------------
# spec_hash field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_hash_matches_spec_module_hash() -> None:
    spec = _extract_spec()
    bundle = compile(spec)
    assert bundle.spec_hash == spec_hash(spec)


@pytest.mark.unit
def test_spec_hash_is_hex_sha256() -> None:
    bundle = compile(_extract_spec())
    assert isinstance(bundle.spec_hash, str)
    assert len(bundle.spec_hash) == 64
    int(bundle.spec_hash, 16)  # would raise if non-hex


# ---------------------------------------------------------------------------
# Determinism — same spec compiled twice yields equal artefacts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_determinism_extract_prompt_equal() -> None:
    spec = _extract_spec()
    a = compile(spec)
    b = compile(spec)
    assert a.prompt == b.prompt


@pytest.mark.unit
def test_determinism_extract_spec_hash_equal() -> None:
    spec = _extract_spec()
    a = compile(spec)
    b = compile(spec)
    assert a.spec_hash == b.spec_hash


@pytest.mark.unit
def test_determinism_extract_tool_schema_equal() -> None:
    spec = _extract_spec()
    a = compile(spec)
    b = compile(spec)
    assert dict(a.tool_schema_per_backend) == dict(b.tool_schema_per_backend)


@pytest.mark.unit
def test_determinism_extract_pydantic_schema_equal() -> None:
    """``create_model`` returns a fresh class object on each call, so class
    identity differs. Determinism is asserted at the schema layer."""
    spec = _extract_spec()
    a = compile(spec)
    b = compile(spec)
    assert a.pydantic_cls.model_json_schema() == b.pydantic_cls.model_json_schema()


@pytest.mark.unit
def test_determinism_classify_prompt_equal() -> None:
    spec = _classify_spec(allow_multi_label=True)
    a = compile(spec)
    b = compile(spec)
    assert a.prompt == b.prompt


@pytest.mark.unit
def test_determinism_classify_spec_hash_equal() -> None:
    spec = _classify_spec(allow_multi_label=True)
    a = compile(spec)
    b = compile(spec)
    assert a.spec_hash == b.spec_hash


@pytest.mark.unit
def test_determinism_classify_pydantic_schema_equal() -> None:
    spec = _classify_spec(allow_multi_label=True)
    a = compile(spec)
    b = compile(spec)
    assert a.pydantic_cls.model_json_schema() == b.pydantic_cls.model_json_schema()


# ---------------------------------------------------------------------------
# Round-trip across both supported task types
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoice_extract_spec_round_trips() -> None:
    bundle = compile(_extract_spec())
    instance = bundle.pydantic_cls.model_validate(
        {
            "invoice_number": "INV-001",
            "total_amount": "99.00",
            "currency": "EUR",
            "notes": "paid in full",
            "billing": {"city": "Paris", "zip": "75001"},
        }
    )
    assert instance is not None


@pytest.mark.unit
def test_email_category_classify_spec_round_trips() -> None:
    bundle = compile(_classify_spec(allow_multi_label=False))
    instance = bundle.pydantic_cls.model_validate({"label": "billing"})
    assert instance is not None


@pytest.mark.unit
def test_email_category_classify_spec_multi_label_round_trips() -> None:
    bundle = compile(_classify_spec(allow_multi_label=True))
    instance = bundle.pydantic_cls.model_validate({"labels": ["billing", "support"]})
    assert instance is not None


# ---------------------------------------------------------------------------
# Performance — PRD.md L375 budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compile_extract_under_perf_budget() -> None:
    """Warm-up the path once so the timed call measures compile work rather
    than first-import overhead."""
    spec = _extract_spec()
    compile(spec)  # warm-up
    start = time.perf_counter()
    compile(spec)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 200.0, f"compile took {elapsed_ms:.1f} ms (>200 ms budget)"


@pytest.mark.unit
def test_compile_classify_under_perf_budget() -> None:
    spec = _classify_spec(allow_multi_label=True)
    compile(spec)  # warm-up
    start = time.perf_counter()
    compile(spec)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 200.0, f"compile took {elapsed_ms:.1f} ms (>200 ms budget)"


# ---------------------------------------------------------------------------
# Purity — compile must not mutate the input spec
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compile_does_not_mutate_input_spec() -> None:
    spec = _extract_spec()
    before = spec.model_dump()
    compile(spec)
    after = spec.model_dump()
    assert before == after


# ---------------------------------------------------------------------------
# Compile-side memoization — PLAN.md B1
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compile_cache_info_exposed() -> None:
    """compile_cache_info() exposes hits/misses/currsize as ints."""
    compile_cache_clear()
    info = compile_cache_info()
    assert isinstance(info.hits, int)
    assert isinstance(info.misses, int)
    assert isinstance(info.currsize, int)
    assert info.hits == 0
    assert info.misses == 0
    assert info.currsize == 0


@pytest.mark.unit
def test_second_compile_on_field_equal_spec_is_cache_hit() -> None:
    """B1 exit: second compile on a field-equal spec is a cache hit."""
    compile_cache_clear()
    compile(_extract_spec())
    compile(_extract_spec())
    info = compile_cache_info()
    assert info.misses == 1
    assert info.hits == 1
    assert info.currsize == 1


@pytest.mark.unit
def test_cache_hit_returns_field_equal_artefacts() -> None:
    """B1 exit: cache hit preserves the determinism contract (field-equal)."""
    compile_cache_clear()
    first = compile(_extract_spec())
    second = compile(_extract_spec())
    assert second.prompt == first.prompt
    assert second.spec_hash == first.spec_hash
    assert second.max_input_tokens == first.max_input_tokens
    assert second.pydantic_cls.model_json_schema() == first.pydantic_cls.model_json_schema()


@pytest.mark.unit
def test_distinct_specs_do_not_collide_in_cache() -> None:
    """Distinct specs occupy distinct cache slots — no false hit."""
    compile_cache_clear()
    compile(_extract_spec())
    compile(_classify_spec(allow_multi_label=True))
    info = compile_cache_info()
    assert info.misses == 2
    assert info.hits == 0
    assert info.currsize == 2


@pytest.mark.unit
def test_compile_cache_clear_resets_counters_and_store() -> None:
    """compile_cache_clear() empties the store and zeroes the counters."""
    compile(_extract_spec())
    compile_cache_clear()
    info = compile_cache_info()
    assert info.hits == 0
    assert info.misses == 0
    assert info.currsize == 0
