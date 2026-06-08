"""prompiler — spec-to-artefact prompt compiler."""

from __future__ import annotations

__version__ = "0.1.1"

COMPILER_PROTOCOL_VERSION = "1"
"""Compiler protocol version used in spec_hash derivation.

Bumped only when one of the following changes in a way that would
alter the canonical artefact produced from an unchanged spec:

- The EntitySpec AST grammar (field types, constraints, structure).
- The per-adapter projection schema (tool-call schema layout).
- The canonical-YAML serialisation rules used for hashing.

Patch and minor releases of the ``prompiler`` package that do not
touch any of the above must leave this constant unchanged so that
cached artefacts remain valid across upgrades. See docs/RULES.md §4
for the spec_hash formula.
"""

from prompiler.compiler import ArtefactBundle  # noqa: E402
from prompiler.compiler import compile_spec as compile  # noqa: E402
from prompiler.runtime import run, run_batch, run_sync  # noqa: E402

__all__ = [
    "COMPILER_PROTOCOL_VERSION",
    "ArtefactBundle",
    "__version__",
    "compile",
    "run",
    "run_batch",
    "run_sync",
]
