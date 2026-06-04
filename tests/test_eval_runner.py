"""Unit tests for prompiler.eval.runner — fixture-driven eval (P4).

Drives ``run_eval`` against an in-memory registered spec and the shared
conftest ``ScriptedAdapter`` (no network, no API key). Coverage:

  - happy path: every expected field matches -> all TP
  - mismatch: predicted value differs -> FP + FN on that field
  - missing: predicted field absent / blank -> FN
  - per-case PrompilerError isolation: error recorded, expected -> missing
  - aggregate metrics fold across cases
  - EvalResult / CaseResult / FieldDiff shape + immutability
"""

from __future__ import annotations

import dataclasses

import pytest

from conftest import ScriptedAdapter
from prompiler.backends.observability import PricingEntry, PricingTable
from prompiler.compiler import compile_spec
from prompiler.eval.fixtures import FixtureCase
from prompiler.eval.runner import (
    CapturingHook,
    CaseResult,
    EvalResult,
    EvalUsage,
    FieldDiff,
    run_eval,
)
from prompiler.runtime.errors import AdapterError
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _build_spec(name: str) -> EntitySpec:
    return EntitySpec.model_validate(
        {
            "spec_version": 1,
            "name": name,
            "task": "extract",
            "fields": [
                {"name": "title", "type": "string", "required": True},
                {"name": "author", "type": "string", "required": False},
            ],
        }
    )


def _register(name: str) -> Registry:
    registry = Registry()
    registry.register(name, compile_spec(_build_spec(name)))
    return registry


def _case(name: str, **expected: str) -> FixtureCase:
    return FixtureCase(name=name, input=f"input for {name}", expected=expected)


# ---------------------------------------------------------------------------
# run_eval — status classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_eval_all_match() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": "Herbert"}])
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    assert isinstance(result, EvalResult)
    assert result.metrics.tp == 2
    assert result.metrics.fp == 0
    assert result.metrics.fn == 0
    assert result.metrics.f1 == 1.0
    assert all(d.status == "match" for d in result.cases[0].diffs)


@pytest.mark.unit
def test_run_eval_mismatch() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": "WRONG"}])
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    statuses = {d.field: d.status for d in result.cases[0].diffs}
    assert statuses == {"title": "match", "author": "mismatch"}
    # mismatch contributes one FP and one FN
    assert result.metrics.tp == 1
    assert result.metrics.fp == 1
    assert result.metrics.fn == 1


@pytest.mark.unit
def test_run_eval_missing_field() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": None}])
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    statuses = {d.field: d.status for d in result.cases[0].diffs}
    assert statuses == {"title": "match", "author": "missing"}
    assert result.metrics.tp == 1
    assert result.metrics.fp == 0
    assert result.metrics.fn == 1


@pytest.mark.unit
def test_run_eval_blank_prediction_is_missing() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": "   "}])
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    statuses = {d.field: d.status for d in result.cases[0].diffs}
    assert statuses["author"] == "missing"


# ---------------------------------------------------------------------------
# run_eval — error isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_eval_per_case_error_isolated() -> None:
    registry = _register("books")
    # case 1 raises; case 2 succeeds. One broken case must not abort the run.
    adapter = ScriptedAdapter(
        [
            AdapterError("backend exploded"),
            {"title": "Dune", "author": "Herbert"},
        ]
    )
    cases = [
        _case("boom", title="X", author="Y"),
        _case("ok", title="Dune", author="Herbert"),
    ]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    boom, ok = result.cases
    assert boom.error is not None
    assert "backend exploded" in boom.error
    assert {d.status for d in boom.diffs} == {"missing"}
    assert ok.error is None
    assert all(d.status == "match" for d in ok.diffs)
    # boom -> 2 FN; ok -> 2 TP
    assert result.metrics.tp == 2
    assert result.metrics.fn == 2


@pytest.mark.unit
def test_run_eval_empty_cases() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([])

    result = run_eval("books", [], backend=adapter, registry=registry)

    assert result.cases == ()
    assert result.metrics.tp == 0
    assert result.metrics.f1 == 0.0


# ---------------------------------------------------------------------------
# Shape / immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_field_diff_records_values() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": "WRONG"}])
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    author = next(d for d in result.cases[0].diffs if d.field == "author")
    assert author.expected == "Herbert"
    assert author.predicted == "WRONG"


@pytest.mark.unit
def test_eval_result_frozen() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": "Herbert"}])
    result = run_eval("books", [_case("c1", title="Dune")], backend=adapter, registry=registry)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.metrics = None  # type: ignore[misc]


@pytest.mark.unit
def test_field_diff_frozen() -> None:
    diff = FieldDiff(field="x", expected="a", predicted="a", status="match")
    with pytest.raises(dataclasses.FrozenInstanceError):
        diff.status = "missing"  # type: ignore[misc]


@pytest.mark.unit
def test_case_result_default_error_none() -> None:
    result = CaseResult(name="c1", diffs=())
    assert result.error is None


# ---------------------------------------------------------------------------
# Cost + token accounting (opt-in via metrics_hook)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_usage_none_without_metrics_hook() -> None:
    registry = _register("books")
    adapter = ScriptedAdapter([{"title": "Dune", "author": "Herbert"}])
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry)

    assert result.usage is None


@pytest.mark.unit
def test_usage_totals_token_counts() -> None:
    registry = _register("books")
    hook = CapturingHook()
    adapter = ScriptedAdapter(
        [
            {"title": "Dune", "author": "Herbert"},
            {"title": "Hyperion", "author": "Simmons"},
        ],
        observability=hook,
        tokens=[(100, 50), (200, 75)],
    )
    cases = [
        _case("c1", title="Dune", author="Herbert"),
        _case("c2", title="Hyperion", author="Simmons"),
    ]

    result = run_eval("books", cases, backend=adapter, registry=registry, metrics_hook=hook)

    assert result.usage is not None
    assert result.usage.calls == 2
    assert result.usage.prompt_tokens == 300
    assert result.usage.completion_tokens == 125


@pytest.mark.unit
def test_usage_sums_cost_from_pricing_table() -> None:
    registry = _register("books")
    pricing = PricingTable(rates={("scripted", "test-model"): PricingEntry(2.0, 6.0)})
    hook = CapturingHook()
    adapter = ScriptedAdapter(
        [{"title": "Dune", "author": "Herbert"}],
        observability=hook,
        pricing=pricing,
        tokens=[(100, 50)],
    )
    cases = [_case("c1", title="Dune", author="Herbert")]

    result = run_eval("books", cases, backend=adapter, registry=registry, metrics_hook=hook)

    # 100 * 2.0/1e6 + 50 * 6.0/1e6 = 0.0002 + 0.0003 = 0.0005
    assert result.usage is not None
    assert result.usage.cost_usd == pytest.approx(0.0005)


@pytest.mark.unit
def test_eval_usage_frozen() -> None:
    usage = EvalUsage(calls=1, prompt_tokens=10, completion_tokens=5, cost_usd=0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        usage.calls = 2  # type: ignore[misc]
