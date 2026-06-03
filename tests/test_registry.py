"""Unit tests for prompiler.registry — in-process Registry (P3 task 1).

Scope (PLAN.md L157, verbatim):
  "In-process registry (`prompiler.registry`)."

Shape contract (architecture.md L265, verbatim):
  "In-process dict `name -> ArtefactBundle`. File-system discovery scans
  `prompts/` on startup ... Programmatic registration via
  `register_from_path()` and `register_from_dict()`. Hash collision warns;
  duplicate name raises."

Name pattern (architecture.md L406, S5 — verbatim):
  "Resource name must match `^[a-z0-9_-]+$`; lookup goes through the
  registry, never the file system."

Public surface (architecture.md L117, verbatim):
  "from prompiler.registry import register_from_path, register_from_dict, get"

This file pins the *sub-step-1* Registry API (no discovery, no programmatic
helpers yet — those are sub-steps 2/3). Locked down here so later sub-steps
cannot silently relax it:

- ``Registry.register(name, bundle)`` — store under ``name``.
- ``Registry.get(name) -> ArtefactBundle`` — exact, no fuzzy match.
- ``Registry.names() -> frozenset[str]`` — immutable snapshot.
- ``name in registry`` — membership check.
- Missing name -> ``KeyError`` (not ``None`` — silent miss is the LL-004
  failure mode the registry exists to prevent).
- Duplicate name -> ``ValueError`` (architecture.md L265: "duplicate
  name raises"; no silent overwrite path).
- Invalid name -> ``ValueError`` at register time (S5: enforced at the
  registry boundary, not deferred to MCP layer).
"""

from __future__ import annotations

import pytest

from prompiler.compiler import compile_spec
from prompiler.registry import Registry
from prompiler.spec import EntitySpec

INVOICE_SPEC: dict[str, object] = {
    "spec_version": 1,
    "name": "invoice",
    "task": "extract",
    "description": "Extract invoice fields.",
    "fields": [
        {"name": "total", "type": "decimal", "required": True},
    ],
}


def _invoice_bundle():
    spec = EntitySpec.model_validate(INVOICE_SPEC)
    return compile_spec(spec)


@pytest.mark.unit
def test_register_and_get_round_trip() -> None:
    reg = Registry()
    bundle = _invoice_bundle()

    reg.register("invoice", bundle)

    assert reg.get("invoice") is bundle


@pytest.mark.unit
def test_get_missing_raises_keyerror() -> None:
    reg = Registry()

    with pytest.raises(KeyError) as exc_info:
        reg.get("missing")

    assert "missing" in str(exc_info.value)


@pytest.mark.unit
def test_duplicate_register_raises_valueerror() -> None:
    reg = Registry()
    bundle_a = _invoice_bundle()
    bundle_b = _invoice_bundle()
    reg.register("invoice", bundle_a)

    with pytest.raises(ValueError) as exc_info:
        reg.register("invoice", bundle_b)

    message = str(exc_info.value)
    assert "invoice" in message
    assert "already registered" in message.lower() or "duplicate" in message.lower()
    assert reg.get("invoice") is bundle_a


@pytest.mark.unit
def test_names_returns_frozenset_snapshot() -> None:
    reg = Registry()
    reg.register("invoice", _invoice_bundle())

    names = reg.names()
    assert isinstance(names, frozenset)
    assert names == frozenset({"invoice"})

    reg.register("receipt", _invoice_bundle())
    assert names == frozenset({"invoice"})
    assert reg.names() == frozenset({"invoice", "receipt"})


@pytest.mark.unit
def test_contains_membership() -> None:
    reg = Registry()
    reg.register("invoice", _invoice_bundle())

    assert "invoice" in reg
    assert "missing" not in reg


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_name",
    [
        "Invoice",
        "invoice.v2",
        "invoice/v2",
        "invoice v2",
        "",
        "../etc/passwd",
        "invoice!",
        "café",
    ],
)
def test_invalid_name_rejected_at_register(bad_name: str) -> None:
    reg = Registry()

    with pytest.raises(ValueError) as exc_info:
        reg.register(bad_name, _invoice_bundle())

    assert bad_name not in reg
    assert "name" in str(exc_info.value).lower()


@pytest.mark.unit
@pytest.mark.parametrize(
    "good_name",
    ["invoice", "invoice-v2", "invoice_v2", "inv01", "a", "0", "_", "-"],
)
def test_valid_name_accepted(good_name: str) -> None:
    reg = Registry()
    bundle = _invoice_bundle()

    reg.register(good_name, bundle)

    assert reg.get(good_name) is bundle


@pytest.mark.unit
def test_empty_registry_names_is_empty_frozenset() -> None:
    reg = Registry()
    assert reg.names() == frozenset()
    assert "anything" not in reg
