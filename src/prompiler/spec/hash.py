"""Canonical spec hash for cache-key derivation (P1.3).

Implements RULES.md §10 + PRD §5:

    spec_hash = SHA-256(canonical_yaml(spec) || COMPILER_PROTOCOL_VERSION)

where ``||`` is string concatenation prior to UTF-8 encoding. Cached
artefacts are keyed by this digest; the protocol version is mixed in so
a bump invalidates every cache entry simultaneously without requiring
the spec source to change.
"""

from __future__ import annotations

import hashlib
from typing import Any

import yaml

from prompiler import COMPILER_PROTOCOL_VERSION
from prompiler.spec.model import EntitySpec


def canonical_yaml(spec: EntitySpec) -> str:
    """Serialise *spec* to canonical YAML.

    Mapping keys are sorted; list-element order is preserved because it
    is semantic for ``fields`` and ``labels``. Pydantic defaults are
    emitted explicitly so two specs that differ only in which fields
    were elided in the source YAML still hash the same.
    """
    payload: dict[str, Any] = spec.model_dump(mode="json", exclude_defaults=False)
    return yaml.safe_dump(
        payload,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def spec_hash(spec: EntitySpec) -> str:
    """Return the hex64 SHA-256 digest of *spec* under the current protocol."""
    body = canonical_yaml(spec) + COMPILER_PROTOCOL_VERSION
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
