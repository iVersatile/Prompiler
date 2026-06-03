"""Run-time error hierarchy — sub-step 4a (PLAN.md L160).

Shape (architecture.md L268-277, verbatim):

  PrompilerError
  ├── SpecError           # bad spec at parse/lint time
  ├── CompileError        # bad spec at compile time
  ├── AdapterError        # vendor API problem (network, auth, refusal)
  ├── ExtractionFailed    # validation failure after retry budget
  ├── EvalError           # fixture parse / metric computation
  └── MCPError            # MCP transport problem

This module is the *single raise-target* for every prompiler-originated
run-time failure. LL-004 (single source of truth for run-time spec
lookup — orchestrator must surface failures loudly) is the load-bearing
reason the hierarchy is sealed here at 4a rather than grown organically
inside the orchestrator: by the time sub-step 4b composes the retry-once
loop, ``ExtractionFailed`` already exists as a stable raise-target with
its ``__cause__`` contract pinned by ``tests/test_runtime_errors.py``.

Design choices and the invariants they imply:

- Every class subclasses ``PrompilerError``; ``PrompilerError`` subclasses
  ``Exception`` (not ``BaseException``). One ``except PrompilerError``
  clause drains every prompiler failure, and generic ``except Exception``
  still works for callers outside the package.
- Each class uses the standard ``Exception.__init__`` signature. No
  ``__init__`` overrides, no custom attribute plumbing. That keeps
  ``raise ExtractionFailed("msg") from validation_err`` chaining through
  ``__cause__`` without per-class machinery — the contract sub-step 4b
  relies on for surfacing the originating Pydantic ``ValidationError``
  (architecture.md L217).
- Class bodies are intentionally empty. Per-leaf payloads (e.g. a
  structured ``vendor`` / ``status_code`` on ``AdapterError``) belong to
  4b/MCP/Eval phases once the call-sites that need them exist. YAGNI here
  is the right call — adding fields now would couple this module to
  consumers that haven't been written.
- ``__all__`` enumerates exactly the seven names. Anything beyond the
  documented hierarchy is a leak; downstream phases must compose on these
  classes, not invent siblings.
"""

from __future__ import annotations

__all__ = [
    "AdapterError",
    "CompileError",
    "EvalError",
    "ExtractionFailed",
    "MCPError",
    "PrompilerError",
    "SpecError",
]


class PrompilerError(Exception):
    """Base class for every prompiler-originated run-time failure.

    Subclasses ``Exception`` so generic ``except Exception`` drains
    prompiler failures the same way it drains stdlib failures.
    """


class SpecError(PrompilerError):
    """Bad spec at parse/lint time (architecture.md L269)."""


class CompileError(PrompilerError):
    """Bad spec at compile time (architecture.md L270)."""


class AdapterError(PrompilerError):
    """Vendor API problem — network, auth, refusal (architecture.md L271)."""


class ExtractionFailed(PrompilerError):
    """Validation failure after the retry budget (architecture.md L272).

    Sub-step 4b raises this with ``from validation_err`` so the
    originating ``pydantic.ValidationError`` survives via ``__cause__``.
    The standard ``Exception.__init__`` signature is preserved precisely
    so that chaining works without custom plumbing here.
    """


class EvalError(PrompilerError):
    """Fixture parse or metric-computation failure (architecture.md L273)."""


class MCPError(PrompilerError):
    """MCP transport problem (architecture.md L274)."""
