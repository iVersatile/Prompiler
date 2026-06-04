"""Integration test: PRD §4 use case 5 — ICD-10 multi-label classify.

Exercises the classify task with ``allow_multi_label=true``:
- output shape is ``{"labels": [<name>, ...]}`` (not the single-label ``{"label": ...}``),
- compiled model has a single ``labels`` field typed as ``list[Literal[...]]``,
- one out-of-vocabulary entry in the list fails ``Literal`` validation and
  surfaces via the retry → ExtractionFailed path,
- single-label payload (``{"label": ...}``) is rejected by the multi-label spec.

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

import pytest
from pydantic import BaseModel, ValidationError

from conftest import ScriptedAdapter
from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_RETRY_BUDGET: Final[int] = 2

_ICD10_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "icd10_codes",
    "task": "classify",
    "description": "Assign ICD-10 codes that apply to the clinical note.",
    "labels": [
        {"name": "E11.9", "description": "Type 2 diabetes mellitus without complications."},
        {"name": "I10", "description": "Essential (primary) hypertension."},
        {"name": "J45.909", "description": "Unspecified asthma, uncomplicated."},
        {"name": "K21.9", "description": "Gastro-esophageal reflux disease without esophagitis."},
    ],
    "allow_multi_label": True,
}


def _register() -> Registry:
    registry = Registry()
    registry.register("icd10_codes", compile_spec(EntitySpec.model_validate(_ICD10_SPEC)))
    return registry


@pytest.mark.integration
def test_icd10_classify_happy_path_returns_multiple_labels() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"labels": ["E11.9", "I10"]}])

    result = asyncio.run(
        run("icd10_codes", "65yo with T2DM and HTN", backend=adapter, registry=registry)
    )

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.labels == ["E11.9", "I10"]


@pytest.mark.integration
def test_icd10_classify_accepts_single_element_list() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"labels": ["J45.909"]}])

    result = asyncio.run(
        run("icd10_codes", "asthma exacerbation", backend=adapter, registry=registry)
    )

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.labels == ["J45.909"]


@pytest.mark.integration
def test_icd10_classify_rejects_out_of_vocab_label_in_list() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"labels": ["E11.9", "Z99.99"]}] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("icd10_codes", "mixed codes", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("labels" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())


@pytest.mark.integration
def test_icd10_classify_rejects_single_label_payload_when_multi_label_spec() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"label": "E11.9"}] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("icd10_codes", "wrong shape", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any(err["type"] == "missing" for err in cause.errors())
