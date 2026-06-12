"""Unit tests for prompiler.registry — sub-step 3 (PLAN.md L158).

Scope (architecture.md L265, verbatim):
  "File-system discovery scans ``prompts/`` on startup (configurable via
  ``pyproject.toml [tool.prompiler]``). ... Hash collision warns;
  duplicate name raises."

Config precedence (architecture.md L437-442):
  kwarg > ``PROMPILER_PROMPTS_DIR`` env > ``pyproject.toml
  [tool.prompiler] prompts_dir`` > built-in default ``"prompts"``.

Public surface added this sub-step:
  ``discover(prompts_dir=None, *, registry=None) -> list[ArtefactBundle]``

Pins behaviour later layers (MCP startup, CLI) rely on:

- Spec ``name`` field (not filename) is the registry key (S5 — single
  source of truth).
- ``.yaml`` and ``.yml`` extensions both scanned; non-YAML files
  ignored; sub-directories not walked (flat scan).
- Missing ``prompts/`` directory is tolerated (returns ``[]``) — the
  orchestrator must boot without a prompts dir for programmatic-only
  callers.
- Malformed YAML surfaces ``SpecLoadError`` (file/line/column from
  ``load_spec``), not a silent skip.
- Duplicate ``name`` across two files raises ``ValueError`` at register
  time (architecture.md L265 — "duplicate name raises").
- Distinct ``name`` keys mapping to bundles with the same
  ``spec_hash`` emit a ``RuntimeWarning`` (architecture.md L265 — "hash
  collision warns") but both still register. Natural collisions are
  impossible because ``name`` is part of canonical YAML, so the test
  forces a collision by monkeypatching ``compile_spec`` in the
  ``prompiler.registry`` namespace.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
import yaml

import prompiler.registry as registry_module
from prompiler.compiler import ArtefactBundle
from prompiler.registry import Registry, discover
from prompiler.spec import SpecLoadError

INVOICE_SPEC: dict[str, object] = {
    "spec_version": 2,
    "name": "invoice",
    "task": "extract",
    "description": "Extract invoice fields.",
    "fields": [
        {"name": "total", "type": "decimal", "required": True},
    ],
}

RECEIPT_SPEC: dict[str, object] = {
    "spec_version": 2,
    "name": "receipt",
    "task": "extract",
    "description": "Extract receipt fields.",
    "fields": [
        {"name": "amount", "type": "decimal", "required": True},
    ],
}


@pytest.fixture
def fresh_default_registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    """Swap module singleton for a fresh instance — mirrors test_registry.py:207."""
    fresh = Registry()
    monkeypatch.setattr(registry_module, "_DEFAULT_REGISTRY", fresh)
    return fresh


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip PROMPILER_PROMPTS_DIR so host env never leaks into a test."""
    monkeypatch.delenv("PROMPILER_PROMPTS_DIR", raising=False)


def _write_spec(dir_path: Path, filename: str, spec: dict[str, object]) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / filename
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path & basic scanning
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_registers_all_yaml_specs(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)
    _write_spec(prompts, "receipt.yaml", RECEIPT_SPEC)

    bundles = discover(prompts, registry=reg)

    assert isinstance(bundles, list)
    assert len(bundles) == 2
    assert all(isinstance(b, ArtefactBundle) for b in bundles)
    assert reg.names() == frozenset({"invoice", "receipt"})


@pytest.mark.unit
def test_discover_uses_spec_name_not_filename(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "totally-different-filename.yaml", INVOICE_SPEC)

    discover(prompts, registry=reg)

    assert "invoice" in reg
    assert "totally-different-filename" not in reg


@pytest.mark.unit
def test_discover_accepts_yml_extension(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yml", INVOICE_SPEC)

    bundles = discover(prompts, registry=reg)

    assert len(bundles) == 1
    assert "invoice" in reg


@pytest.mark.unit
def test_discover_accepts_str_path(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)

    bundles = discover(str(prompts), registry=reg)

    assert len(bundles) == 1
    assert "invoice" in reg


@pytest.mark.unit
def test_discover_skips_non_yaml_files(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)
    (prompts / "README.md").write_text("notes", encoding="utf-8")
    (prompts / "notes.txt").write_text("hello", encoding="utf-8")
    (prompts / "data.json").write_text("{}", encoding="utf-8")

    bundles = discover(prompts, registry=reg)

    assert len(bundles) == 1
    assert reg.names() == frozenset({"invoice"})


@pytest.mark.unit
def test_discover_does_not_recurse_into_subdirectories(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)
    nested = prompts / "nested"
    _write_spec(nested, "receipt.yaml", RECEIPT_SPEC)

    bundles = discover(prompts, registry=reg)

    assert len(bundles) == 1
    assert reg.names() == frozenset({"invoice"})


# ---------------------------------------------------------------------------
# Tolerated and error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_missing_directory_returns_empty(tmp_path: Path) -> None:
    reg = Registry()
    missing = tmp_path / "does-not-exist"

    bundles = discover(missing, registry=reg)

    assert bundles == []
    assert reg.names() == frozenset()


@pytest.mark.unit
def test_discover_empty_directory_returns_empty(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    prompts.mkdir()

    bundles = discover(prompts, registry=reg)

    assert bundles == []
    assert reg.names() == frozenset()


@pytest.mark.unit
def test_discover_malformed_yaml_raises_spec_load_error(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "broken.yaml").write_text("name: invoice\n: bad indent\n", encoding="utf-8")

    with pytest.raises(SpecLoadError):
        discover(prompts, registry=reg)


@pytest.mark.unit
def test_discover_duplicate_name_raises_value_error(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "a.yaml", INVOICE_SPEC)
    _write_spec(prompts, "b.yaml", INVOICE_SPEC)

    with pytest.raises(ValueError) as exc_info:
        discover(prompts, registry=reg)

    assert "invoice" in str(exc_info.value)


@pytest.mark.unit
def test_discover_invalid_spec_name_raises_value_error(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    bad = dict(INVOICE_SPEC)
    bad["name"] = "Invoice"
    _write_spec(prompts, "bad.yaml", bad)

    with pytest.raises(ValueError):
        discover(prompts, registry=reg)


# ---------------------------------------------------------------------------
# Hash collision warning (architecture.md L265 — "hash collision warns")
# ---------------------------------------------------------------------------
#
# Two distinct names mapping to bundles with the same ``spec_hash`` must
# emit ``RuntimeWarning`` but both still register. Natural collisions are
# impossible (name is part of canonical YAML), so we force the situation
# by monkeypatching ``compile_spec`` in the ``prompiler.registry``
# namespace to return a bundle stamped with a fixed hash.


@pytest.mark.unit
def test_discover_hash_collision_emits_runtime_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from prompiler.compiler import compile_spec as real_compile_spec

    forced_hash = "deadbeef" * 8

    def forced_compile_spec(spec: object) -> ArtefactBundle:
        bundle = real_compile_spec(spec)  # type: ignore[arg-type]
        return replace(bundle, spec_hash=forced_hash)

    monkeypatch.setattr(registry_module, "compile_spec", forced_compile_spec)

    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)
    _write_spec(prompts, "receipt.yaml", RECEIPT_SPEC)

    with pytest.warns(RuntimeWarning, match="hash"):
        bundles = discover(prompts, registry=reg)

    assert len(bundles) == 2
    assert reg.names() == frozenset({"invoice", "receipt"})


@pytest.mark.unit
def test_discover_no_collision_no_warning(tmp_path: Path) -> None:
    reg = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)
    _write_spec(prompts, "receipt.yaml", RECEIPT_SPEC)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        bundles = discover(prompts, registry=reg)

    assert len(bundles) == 2


# ---------------------------------------------------------------------------
# Registry injection & default singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_uses_default_registry_when_none(
    tmp_path: Path, fresh_default_registry: Registry
) -> None:
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)

    bundles = discover(prompts)

    assert len(bundles) == 1
    assert "invoice" in fresh_default_registry


@pytest.mark.unit
def test_discover_injected_registry_does_not_touch_default(
    tmp_path: Path, fresh_default_registry: Registry
) -> None:
    isolated = Registry()
    prompts = tmp_path / "prompts"
    _write_spec(prompts, "invoice.yaml", INVOICE_SPEC)

    discover(prompts, registry=isolated)

    assert "invoice" in isolated
    assert "invoice" not in fresh_default_registry


# ---------------------------------------------------------------------------
# Config precedence: kwarg > env > pyproject > default "prompts"
# (architecture.md L437-442)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_kwarg_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = tmp_path / "kwarg-prompts"
    _write_spec(chosen, "invoice.yaml", INVOICE_SPEC)
    other = tmp_path / "env-prompts"
    _write_spec(other, "receipt.yaml", RECEIPT_SPEC)
    monkeypatch.setenv("PROMPILER_PROMPTS_DIR", str(other))

    reg = Registry()
    discover(chosen, registry=reg)

    assert "invoice" in reg
    assert "receipt" not in reg


@pytest.mark.unit
def test_discover_env_wins_over_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_dir = tmp_path / "env-prompts"
    _write_spec(env_dir, "invoice.yaml", INVOICE_SPEC)
    pyproject_dir = tmp_path / "pyproject-prompts"
    _write_spec(pyproject_dir, "receipt.yaml", RECEIPT_SPEC)

    (tmp_path / "pyproject.toml").write_text(
        f'[tool.prompiler]\nprompts_dir = "{pyproject_dir}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("PROMPILER_PROMPTS_DIR", str(env_dir))
    monkeypatch.chdir(tmp_path)

    reg = Registry()
    discover(registry=reg)

    assert "invoice" in reg
    assert "receipt" not in reg


@pytest.mark.unit
def test_discover_pyproject_wins_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyproject_dir = tmp_path / "pyproject-prompts"
    _write_spec(pyproject_dir, "invoice.yaml", INVOICE_SPEC)
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.prompiler]\nprompts_dir = "{pyproject_dir}"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    reg = Registry()
    discover(registry=reg)

    assert "invoice" in reg


@pytest.mark.unit
def test_discover_default_is_prompts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_dir = tmp_path / "prompts"
    _write_spec(default_dir, "invoice.yaml", INVOICE_SPEC)
    monkeypatch.chdir(tmp_path)

    reg = Registry()
    discover(registry=reg)

    assert "invoice" in reg


@pytest.mark.unit
def test_discover_pyproject_without_tool_block_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    default_dir = tmp_path / "prompts"
    _write_spec(default_dir, "invoice.yaml", INVOICE_SPEC)
    monkeypatch.chdir(tmp_path)

    reg = Registry()
    discover(registry=reg)

    assert "invoice" in reg


@pytest.mark.unit
def test_discover_missing_pyproject_and_no_env_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_dir = tmp_path / "prompts"
    _write_spec(default_dir, "invoice.yaml", INVOICE_SPEC)
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "pyproject.toml").exists()
    assert "PROMPILER_PROMPTS_DIR" not in os.environ

    reg = Registry()
    discover(registry=reg)

    assert "invoice" in reg


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_listed_in_dunder_all() -> None:
    assert "discover" in registry_module.__all__
