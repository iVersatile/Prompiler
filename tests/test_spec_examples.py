"""Every on-disk example spec must survive the full public pipeline.

Guards gap G5: inline tests exercised 6 spec shapes, but only 2 lived on
disk and none were round-tripped through load -> lint -> hash -> canonical
reload. This parametrizes over `examples/*.yaml` and drives each through the
public surface so the shipped examples can never silently rot.
"""

from __future__ import annotations

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
