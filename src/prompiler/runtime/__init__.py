"""prompiler.runtime — P3 orchestration layer (architecture.md §2.4).

This package houses the run-time pipeline: registry, orchestrator, retry
policy, error hierarchy, and the chunking helper. Sub-step 4a (PLAN.md
L160) lands the *scaffold* — error hierarchy and the package itself —
ahead of the orchestrator (4b) that composes on top.

Public surface owned by this package:

- ``prompiler.runtime.errors`` — the 7-class exception taxonomy from
  architecture.md L268-277. Catchable through a single ``except
  PrompilerError`` clause (LL-004 — single source of truth for run-time
  spec lookup; orchestrator must surface failures loudly).
- ``prompiler.runtime.registry`` — canonical home of the in-process
  ``Registry`` (moved here from ``prompiler.registry`` in this same
  sub-step). The top-level ``prompiler.registry`` module remains as a
  re-export shim because PRD L185 and architecture.md L117 document that
  import path.

Re-exporting the error symbols at the package boundary is deliberate:
callers that catch prompiler failures should be able to write
``from prompiler.runtime import PrompilerError`` instead of reaching
into ``prompiler.runtime.errors``. The submodule remains importable
(``from prompiler.runtime import errors``) for tests and tooling that
introspect ``__all__``.
"""

from __future__ import annotations

from prompiler.runtime.errors import (
    AdapterError,
    CompileError,
    EvalError,
    ExtractionFailed,
    MCPError,
    PrompilerError,
    SpecError,
)

__all__ = [
    "AdapterError",
    "CompileError",
    "EvalError",
    "ExtractionFailed",
    "MCPError",
    "PrompilerError",
    "SpecError",
]
