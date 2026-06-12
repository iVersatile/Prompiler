"""Integration test: PRD §5.1 email_category use case — classify (single-label).

Exercises the classify task with ``allow_multi_label=false``:
- output shape is ``{"label": <name>}``,
- compiled model has a single ``label`` field typed as ``Literal[...]``,
- any out-of-vocabulary label fails ``Literal`` validation and triggers the
  retry → ExtractionFailed path.

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

_EMAIL_SPEC: Final[dict[str, Any]] = {
    "spec_version": 2,
    "name": "email_category",
    "task": "classify",
    "description": "Route inbound support email into one routing bucket.",
    "labels": [
        {"name": "billing", "description": "Payments, invoices, refunds, subscription changes."},
        {
            "name": "technical",
            "description": "Product not working, bugs, performance, integration failures.",
        },
        {"name": "sales", "description": "Pre-purchase questions, demo requests, pricing."},
        {"name": "spam", "description": "Unsolicited promotion, automated noise."},
    ],
    "allow_multi_label": False,
}


def _register() -> Registry:
    registry = Registry()
    registry.register("email_category", compile_spec(EntitySpec.model_validate(_EMAIL_SPEC)))
    return registry


@pytest.mark.integration
def test_email_classify_happy_path_returns_single_label() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"label": "billing"}])

    result = asyncio.run(
        run("email_category", "where is my refund?", backend=adapter, registry=registry)
    )

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.label == "billing"


@pytest.mark.integration
def test_email_classify_rejects_label_outside_vocabulary() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"label": "marketing"}] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("email_category", "promo blast", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("label" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())


@pytest.mark.integration
def test_email_classify_rejects_multi_label_payload_when_single_label_spec() -> None:
    registry = _register()
    adapter = ScriptedAdapter([{"labels": ["billing", "sales"]}] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("email_category", "billing or sales?", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any(err["type"] == "missing" for err in cause.errors())
