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

import re
from pathlib import Path
from typing import Any, Final

from prompiler.compiler import ArtefactBundle, compile_spec
from prompiler.spec import EntitySpec, load_spec

__all__ = [
    "Registry",
    "get",
    "register_from_dict",
    "register_from_path",
]

_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_-]+$")


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
