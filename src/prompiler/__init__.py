"""prompiler — spec-to-artefact prompt compiler."""

__version__ = "0.0.0"

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

__all__ = ["COMPILER_PROTOCOL_VERSION", "__version__"]
