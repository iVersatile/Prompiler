"""In-process registry — sub-step 1 of P3 task 1 (PLAN.md L157).

Shape (architecture.md L265, verbatim):
  "In-process dict ``name -> ArtefactBundle``. File-system discovery
  scans ``prompts/`` on startup ... Programmatic registration via
  ``register_from_path()`` and ``register_from_dict()``. Hash collision
  warns; duplicate name raises."

This module pins the *storage core* — the immutable lookup contract that
later sub-steps (discovery, programmatic helpers) layer on top. The class
exists to enforce three invariants the rest of the runtime relies on:

1. **Exact lookup only.** ``get(name)`` returns the bundle stored under
   ``name`` or raises ``KeyError``. No fuzzy match, no fallback, no
   silent ``None`` — LL-004 (single source of truth for run-time spec
   lookup) forbids any code path that could resolve to a different
   bundle than the one the caller asked for.

2. **No silent overwrite.** Duplicate ``register(name, ...)`` raises
   ``ValueError`` (architecture.md L265). The orchestrator must see a
   loud failure at startup rather than a quietly-shadowed spec at
   request time.

3. **Name shape enforced at the boundary.** ``^[a-z0-9_-]+$`` is checked
   in ``register``, not deferred to the MCP resource layer
   (architecture.md L406, S5). The registry is the first place a name
   crosses an interface, so it is the right place to reject path-
   traversal inputs ("``../etc/passwd``"), case-folded duplicates
   ("``Invoice``"), and whitespace ("``invoice v2``").

Discovery, ``register_from_path``, ``register_from_dict``, and hash-
collision warnings land in later sub-steps. They will compose on top of
the ``register`` / ``get`` primitives defined here.
"""

from __future__ import annotations

import os
import re
import tomllib
import warnings
from pathlib import Path
from typing import Any, Final

from prompiler.compiler import ArtefactBundle, compile_spec
from prompiler.spec import EntitySpec, load_spec

__all__ = [
    "Registry",
    "discover",
    "get",
    "register_from_dict",
    "register_from_path",
]

_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_-]+$")
_PROMPTS_DIR_ENV: Final[str] = "PROMPILER_PROMPTS_DIR"
_DEFAULT_PROMPTS_DIR: Final[str] = "prompts"
_YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yaml", ".yml"})


class Registry:
    """In-process ``name -> ArtefactBundle`` store.

    Read-mostly. Mutations go through ``register`` only. Lookup via
    ``get`` (raising) or ``__contains__`` (boolean). ``names`` returns
    an immutable frozenset snapshot — callers that hold the result
    cannot see later registrations through it.
    """

    __slots__ = ("_bundles",)

    def __init__(self) -> None:
        self._bundles: dict[str, ArtefactBundle] = {}

    def register(self, name: str, bundle: ArtefactBundle) -> None:
        """Store ``bundle`` under ``name``.

        Raises:
            ValueError: ``name`` does not match ``^[a-z0-9_-]+$`` (S5),
                or a bundle is already registered under ``name``
                (architecture.md L265 — duplicate name raises).
        """
        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid registry name {name!r}: must match {_NAME_PATTERN.pattern}")
        if name in self._bundles:
            raise ValueError(f"name {name!r} is already registered")
        self._bundles[name] = bundle

    def get(self, name: str) -> ArtefactBundle:
        """Return the bundle registered under ``name``.

        Raises:
            KeyError: no bundle registered under ``name``. The registry
                refuses to return ``None`` for a missing lookup — LL-004
                forbids silent misses on the run-time spec path.
        """
        try:
            return self._bundles[name]
        except KeyError:
            raise KeyError(f"no bundle registered under {name!r}") from None

    def names(self) -> frozenset[str]:
        """Return an immutable snapshot of currently registered names."""
        return frozenset(self._bundles)

    def __contains__(self, name: object) -> bool:
        return name in self._bundles


_DEFAULT_REGISTRY: Registry = Registry()


def _resolve(registry: Registry | None) -> Registry:
    return registry if registry is not None else _DEFAULT_REGISTRY


def register_from_dict(
    spec_dict: dict[str, Any], *, registry: Registry | None = None
) -> ArtefactBundle:
    """Validate ``spec_dict`` into an ``EntitySpec``, compile, and register.

    The spec's ``name`` field is the registry key (S5 — single source of
    truth). The ``^[a-z0-9_-]+$`` pattern is enforced at the
    ``Registry.register`` boundary; no second validation happens here.

    Raises:
        pydantic.ValidationError: ``spec_dict`` is not a valid ``EntitySpec``.
        ValueError: spec name fails the registry pattern, or a bundle is
            already registered under that name (architecture.md L265).
    """
    spec = EntitySpec.model_validate(spec_dict)
    bundle = compile_spec(spec)
    _resolve(registry).register(spec.name, bundle)
    return bundle


def register_from_path(path: Path | str, *, registry: Registry | None = None) -> ArtefactBundle:
    """Load a YAML spec from ``path``, compile, and register.

    Delegates parsing/validation to ``prompiler.spec.load_spec``; YAML
    syntax and schema errors surface as ``SpecLoadError`` with file/line/
    column populated.

    Raises:
        SpecLoadError: file missing, unreadable, malformed YAML, or fails
            ``EntitySpec`` validation.
        ValueError: spec name fails the registry pattern, or already
            registered.
    """
    spec = load_spec(path)
    bundle = compile_spec(spec)
    _resolve(registry).register(spec.name, bundle)
    return bundle


def get(name: str, *, registry: Registry | None = None) -> ArtefactBundle:
    """Return the bundle registered under ``name``.

    Module-level convenience wrapper around ``Registry.get`` — exposes
    the L117 public surface so callers can write
    ``from prompiler.registry import get``.

    Raises:
        KeyError: no bundle registered under ``name`` (LL-004 forbids
            silent misses).
    """
    return _resolve(registry).get(name)


def _resolve_prompts_dir(prompts_dir: Path | str | None) -> Path:
    """Resolve the prompts directory per architecture.md L437-442.

    Precedence: kwarg → ``PROMPILER_PROMPTS_DIR`` env → ``pyproject.toml``
    ``[tool.prompiler] prompts_dir`` → ``"prompts"``. Missing pyproject
    file or missing keys silently fall through to the next level — the
    block is optional, not required.
    """
    if prompts_dir is not None:
        return Path(prompts_dir)
    env_value = os.environ.get(_PROMPTS_DIR_ENV)
    if env_value:
        return Path(env_value)
    pyproject = Path.cwd() / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
        tool_block = data.get("tool", {}).get("prompiler", {})
        configured = tool_block.get("prompts_dir")
        if isinstance(configured, str):
            return Path(configured)
    return Path(_DEFAULT_PROMPTS_DIR)


def discover(
    prompts_dir: Path | str | None = None, *, registry: Registry | None = None
) -> list[ArtefactBundle]:
    """Scan ``prompts_dir`` for ``*.yaml``/``*.yml`` specs and register each.

    Flat scan — non-recursive. Files sorted by name for deterministic
    ordering. Each spec is compiled and registered under its declared
    ``name`` field. Returns the bundles in registration order.

    If ``spec_hash`` collides with an already-registered bundle under a
    different name, a ``RuntimeWarning`` is emitted (architecture.md L265
    — hash collision warns). Duplicate ``name`` still raises ``ValueError``
    via ``Registry.register``.

    Raises:
        SpecLoadError: a YAML file fails to parse or validate.
        ValueError: duplicate spec ``name`` (registry refuses overwrite).
    """
    resolved = _resolve_prompts_dir(prompts_dir)
    if not resolved.is_dir():
        return []
    target = _resolve(registry)
    results: list[ArtefactBundle] = []
    yaml_files = sorted(p for p in resolved.iterdir() if p.is_file() and p.suffix in _YAML_SUFFIXES)
    for path in yaml_files:
        spec = load_spec(path)
        bundle = compile_spec(spec)
        for existing_name, existing_bundle in target._bundles.items():
            if existing_bundle.spec_hash == bundle.spec_hash and existing_name != spec.name:
                warnings.warn(
                    f"hash collision: {spec.name!r} shares spec_hash with {existing_name!r}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                break
        target.register(spec.name, bundle)
        results.append(bundle)
    return results
