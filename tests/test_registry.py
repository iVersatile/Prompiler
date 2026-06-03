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

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import prompiler.registry as registry_module
from prompiler.compiler import ArtefactBundle, compile_spec
from prompiler.registry import (
    Registry,
    get,
    register_from_dict,
    register_from_path,
)
from prompiler.spec import EntitySpec, SpecLoadError

INVOICE_SPEC: dict[str, object] = {
    "spec_version": 1,
    "name": "invoice",
    "task": "extract",
    "description": "Extract invoice fields.",
    "fields": [
        {"name": "total", "type": "decimal", "required": True},
    ],
}


def _invoice_bundle() -> ArtefactBundle:
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


# ---------------------------------------------------------------------------
# Sub-step 2: programmatic helpers
# ---------------------------------------------------------------------------
#
# Scope (architecture.md L265, verbatim):
#   "Programmatic registration via `register_from_path()` and
#   `register_from_dict()`."
#
# Public surface (architecture.md L117, verbatim):
#   "from prompiler.registry import register_from_path, register_from_dict, get"
#
# Key derivation: spec's ``name`` field is the registry key (S5 — single
# source of truth). Caller does not supply a separate name. The
# ``^[a-z0-9_-]+$`` pattern is enforced at the ``Registry.register``
# boundary, not re-validated in the helper (LL-004 — one validation
# point per invariant).
#
# Isolation: helpers accept an optional ``registry=`` kwarg. Tests
# inject a fresh ``Registry()`` to avoid leaking state through the
# module-level singleton. The singleton itself is exercised by a single
# dedicated test that resets it.


RECEIPT_SPEC: dict[str, object] = {
    "spec_version": 1,
    "name": "receipt",
    "task": "extract",
    "description": "Extract receipt fields.",
    "fields": [
        {"name": "amount", "type": "decimal", "required": True},
    ],
}


@pytest.fixture
def fresh_default_registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    """Swap the module singleton for a fresh instance for the test."""
    fresh = Registry()
    monkeypatch.setattr(registry_module, "_DEFAULT_REGISTRY", fresh)
    return fresh


@pytest.mark.unit
def test_register_from_dict_returns_bundle_and_stores_under_spec_name() -> None:
    reg = Registry()

    bundle = register_from_dict(INVOICE_SPEC, registry=reg)

    assert isinstance(bundle, ArtefactBundle)
    assert reg.get("invoice") is bundle


@pytest.mark.unit
def test_register_from_dict_invalid_spec_raises_validation_error() -> None:
    reg = Registry()
    bad_spec = {"spec_version": 1, "name": "x"}  # missing required fields

    with pytest.raises(ValidationError):
        register_from_dict(bad_spec, registry=reg)

    assert reg.names() == frozenset()


@pytest.mark.unit
def test_register_from_dict_invalid_name_raises_value_error() -> None:
    reg = Registry()
    bad_name_spec = dict(INVOICE_SPEC)
    bad_name_spec["name"] = "Invoice"

    with pytest.raises(ValueError) as exc_info:
        register_from_dict(bad_name_spec, registry=reg)

    assert "name" in str(exc_info.value).lower()
    assert reg.names() == frozenset()


@pytest.mark.unit
def test_register_from_dict_duplicate_raises_value_error() -> None:
    reg = Registry()
    register_from_dict(INVOICE_SPEC, registry=reg)

    with pytest.raises(ValueError) as exc_info:
        register_from_dict(INVOICE_SPEC, registry=reg)

    assert "invoice" in str(exc_info.value)


@pytest.mark.unit
def test_register_from_path_round_trip(tmp_path: Path) -> None:
    reg = Registry()
    spec_path = tmp_path / "invoice.yaml"
    spec_path.write_text(yaml.safe_dump(INVOICE_SPEC), encoding="utf-8")

    bundle = register_from_path(spec_path, registry=reg)

    assert isinstance(bundle, ArtefactBundle)
    assert reg.get("invoice") is bundle


@pytest.mark.unit
def test_register_from_path_accepts_str(tmp_path: Path) -> None:
    reg = Registry()
    spec_path = tmp_path / "invoice.yaml"
    spec_path.write_text(yaml.safe_dump(INVOICE_SPEC), encoding="utf-8")

    bundle = register_from_path(str(spec_path), registry=reg)

    assert reg.get("invoice") is bundle
    assert isinstance(bundle, ArtefactBundle)


@pytest.mark.unit
def test_register_from_path_missing_file_raises_spec_load_error(tmp_path: Path) -> None:
    reg = Registry()
    missing = tmp_path / "nope.yaml"

    with pytest.raises(SpecLoadError):
        register_from_path(missing, registry=reg)

    assert reg.names() == frozenset()


@pytest.mark.unit
def test_register_from_path_invalid_yaml_raises_spec_load_error(tmp_path: Path) -> None:
    reg = Registry()
    spec_path = tmp_path / "broken.yaml"
    spec_path.write_text("name: invoice\n: bad indent\n", encoding="utf-8")

    with pytest.raises(SpecLoadError):
        register_from_path(spec_path, registry=reg)

    assert reg.names() == frozenset()


@pytest.mark.unit
def test_register_from_path_invalid_spec_name_raises_value_error(tmp_path: Path) -> None:
    bad = dict(INVOICE_SPEC)
    bad["name"] = "Invoice"
    spec_path = tmp_path / "bad.yaml"
    spec_path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    reg = Registry()

    with pytest.raises(ValueError):
        register_from_path(spec_path, registry=reg)

    assert reg.names() == frozenset()


@pytest.mark.unit
def test_module_level_get_delegates_to_injected_registry() -> None:
    reg = Registry()
    bundle = register_from_dict(INVOICE_SPEC, registry=reg)

    assert get("invoice", registry=reg) is bundle


@pytest.mark.unit
def test_module_level_get_missing_raises_keyerror() -> None:
    reg = Registry()

    with pytest.raises(KeyError):
        get("missing", registry=reg)


@pytest.mark.unit
def test_injected_registry_does_not_touch_default_singleton(
    fresh_default_registry: Registry,
) -> None:
    isolated = Registry()

    register_from_dict(INVOICE_SPEC, registry=isolated)

    assert "invoice" in isolated
    assert "invoice" not in fresh_default_registry
    assert fresh_default_registry.names() == frozenset()


@pytest.mark.unit
def test_default_registry_used_when_no_registry_arg(
    fresh_default_registry: Registry,
) -> None:
    bundle = register_from_dict(RECEIPT_SPEC)

    assert get("receipt") is bundle
    assert "receipt" in fresh_default_registry


@pytest.mark.unit
def test_default_registry_path_helper(tmp_path: Path, fresh_default_registry: Registry) -> None:
    spec_path = tmp_path / "invoice.yaml"
    spec_path.write_text(yaml.safe_dump(INVOICE_SPEC), encoding="utf-8")

    bundle = register_from_path(spec_path)

    assert get("invoice") is bundle
    assert "invoice" in fresh_default_registry


@pytest.mark.unit
def test_public_exports_listed_in_dunder_all() -> None:
    assert hasattr(registry_module, "__all__")
    assert set(registry_module.__all__) >= {
        "Registry",
        "register_from_dict",
        "register_from_path",
        "get",
    }
