"""Every on-disk example spec must survive the full public pipeline.

Guards gap G5: inline tests exercised 6 spec shapes, but only 2 lived on
disk and none were round-tripped through load -> lint -> hash -> canonical
reload. This parametrizes over `examples/*.yaml` and drives each through the
public surface so the shipped examples can never silently rot.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from prompiler.spec import (
    EntitySpec,
    canonical_yaml,
    lint_spec,
    load_spec,
    spec_hash,
)

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_EXAMPLE_PATHS = sorted(_EXAMPLES_DIR.glob("*.yaml"))
_EXAMPLE_IDS = [p.stem for p in _EXAMPLE_PATHS]

pytestmark = pytest.mark.unit


def test_examples_dir_is_non_empty() -> None:
    assert _EXAMPLE_PATHS, f"no example specs found under {_EXAMPLES_DIR}"


def test_every_example_spec_is_force_included_in_wheel() -> None:
    """Each repo-root example spec must ship inside the wheel.

    The wheel resolves examples via ``importlib.resources`` from
    ``prompiler/examples/`` (see ``_resolve_examples_dir``). hatchling's
    ``force-include`` list cannot glob, so a new ``examples/*.yaml`` added
    without a matching entry would ship a wheel whose MCP server cannot see
    the spec — the exact class of release defect LL-012 records.
    """
    repo_root = _EXAMPLES_DIR.parent
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    included = {Path(src).name for src in force_include}
    on_disk = {p.name for p in _EXAMPLE_PATHS}
    missing = on_disk - included
    assert not missing, f"example specs missing a wheel force-include entry: {sorted(missing)}"


@pytest.mark.parametrize("path", _EXAMPLE_PATHS, ids=_EXAMPLE_IDS)
def test_example_loads(path: Path) -> None:
    spec = load_spec(path)
    assert isinstance(spec, EntitySpec)


@pytest.mark.parametrize("path", _EXAMPLE_PATHS, ids=_EXAMPLE_IDS)
def test_example_lints_clean(path: Path) -> None:
    spec = load_spec(path)
    assert lint_spec(spec) == []


@pytest.mark.parametrize("path", _EXAMPLE_PATHS, ids=_EXAMPLE_IDS)
def test_example_hash_is_idempotent(path: Path) -> None:
    spec = load_spec(path)
    assert spec_hash(spec) == spec_hash(spec)


@pytest.mark.parametrize("path", _EXAMPLE_PATHS, ids=_EXAMPLE_IDS)
def test_example_canonical_reload_is_stable(path: Path) -> None:
    spec = load_spec(path)
    reloaded = EntitySpec.model_validate(yaml.safe_load(canonical_yaml(spec)))
    assert spec_hash(reloaded) == spec_hash(spec)
    assert canonical_yaml(reloaded) == canonical_yaml(spec)
