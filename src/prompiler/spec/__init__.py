"""prompiler.spec - spec data model + loader + hash + linter (P1.1-P1.4)."""

from __future__ import annotations

from prompiler.spec.hash import canonical_yaml, spec_hash
from prompiler.spec.linter import LintIssue, lint_spec
from prompiler.spec.loader import SpecLoadError, load_spec
from prompiler.spec.model import Constraint, EntitySpec, FieldSpec, Label

__all__ = [
    "Constraint",
    "EntitySpec",
    "FieldSpec",
    "Label",
    "LintIssue",
    "SpecLoadError",
    "canonical_yaml",
    "lint_spec",
    "load_spec",
    "spec_hash",
]
