"""Unit tests for prompiler.runtime.errors — sub-step 4a (PLAN.md L160).

Scope (architecture.md L268-277, verbatim):

  PrompilerError
  ├── SpecError           # bad spec at parse/lint time
  ├── CompileError        # bad spec at compile time
  ├── AdapterError        # vendor API problem (network, auth, refusal)
  ├── ExtractionFailed    # validation failure after retry budget
  ├── EvalError           # fixture parse / metric computation
  └── MCPError            # MCP transport problem

This file pins the *shape* of the error hierarchy before any orchestrator
code reaches for it. The shape itself is load-bearing for LL-004 (single
source of truth for run-time spec lookup — orchestrator must surface
failures loudly): every prompiler-originated failure must be catchable
through a single ``except PrompilerError`` clause, and ``ExtractionFailed``
must chain through ``__cause__`` so the originating Pydantic
``ValidationError`` survives the retry-once boundary (architecture.md L217).

Invariants locked here:

- ``PrompilerError`` subclasses ``Exception`` (not ``BaseException``) so
  generic ``except Exception:`` drains prompiler failures the same way it
  drains stdlib failures. The orchestrator never relies on this, but
  callers of ``run_sync`` outside the package will, and breaking it later
  would be a silent contract change.
- Every leaf subclasses ``PrompilerError`` directly and is a distinct
  concrete class. No shared sibling between two leaves (e.g. an
  ``AdapterError`` that is also a ``SpecError``) — the taxonomy from
  architecture.md is flat by design.
- Each class uses the standard ``Exception.__init__`` signature.
  ``raise ExtractionFailed("msg") from err`` must chain through
  ``__cause__`` without custom plumbing (sub-step 4b's retry loop relies
  on the ``from`` chain to surface the underlying ``ValidationError``).
- ``__all__`` exports exactly the seven names. Anything beyond the
  documented hierarchy is a leak — the orchestrator (sub-step 4b) and
  later MCP/Eval phases must compose on these classes, not invent siblings.
"""

from __future__ import annotations

import pytest

from prompiler.runtime.errors import (
    AdapterError,
    CompileError,
    EvalError,
    ExtractionFailed,
    MCPError,
    PrompilerError,
    SpecError,
)


@pytest.mark.unit
def test_prompiler_error_is_an_exception() -> None:
    assert issubclass(PrompilerError, Exception)


@pytest.mark.unit
@pytest.mark.parametrize(
    "leaf",
    [SpecError, CompileError, AdapterError, ExtractionFailed, EvalError, MCPError],
)
def test_every_leaf_is_a_subclass_of_prompiler_error(leaf: type[Exception]) -> None:
    assert issubclass(leaf, PrompilerError)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc_cls",
    [
        PrompilerError,
        SpecError,
        CompileError,
        AdapterError,
        ExtractionFailed,
        EvalError,
        MCPError,
    ],
)
def test_message_is_preserved_via_standard_exception_init(
    exc_cls: type[Exception],
) -> None:
    err = exc_cls("boom")
    assert str(err) == "boom"
    assert err.args == ("boom",)


@pytest.mark.unit
def test_leaves_are_distinct_concrete_classes() -> None:
    leaves = {SpecError, CompileError, AdapterError, ExtractionFailed, EvalError, MCPError}
    assert len(leaves) == 6


@pytest.mark.unit
def test_exception_chaining_preserves_cause() -> None:
    inner = ValueError("inner reason")
    try:
        try:
            raise inner
        except ValueError as caught:
            raise ExtractionFailed("validation failed twice") from caught
    except ExtractionFailed as outer:
        assert outer.__cause__ is inner
    else:
        pytest.fail("ExtractionFailed was not raised")


@pytest.mark.unit
def test_hierarchy_module_exports_exactly_seven_names() -> None:
    from prompiler.runtime import errors

    assert set(errors.__all__) == {
        "PrompilerError",
        "SpecError",
        "CompileError",
        "AdapterError",
        "ExtractionFailed",
        "EvalError",
        "MCPError",
    }
