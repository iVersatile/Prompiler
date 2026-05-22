"""Shared contract test for BackendAdapter implementations.

Source of truth: docs/PLAN.md §P2 acceptance — "All 4 adapters pass a shared
'happy-path extract' contract test." This file defines that shared test.

Parameterized over adapter factories so every adapter (mock today; claude /
openai / gemini / ollama in later P2.x tasks) runs the same assertions:

  1. Instance satisfies the ``BackendAdapter`` runtime-checkable Protocol.
  2. ``await adapter.extract(...)`` returns a ``dict``.
  3. Every key listed in ``json_schema['required']`` appears in the result.
  4. Two successive calls with identical inputs return identical dicts
     (determinism — needed so MockAdapter can stand in for golden tests and
     so cassette-replayed real adapters stay byte-stable).

Adapter factories are added to ``ADAPTER_FACTORIES`` as each real adapter
lands; the contract assertions never change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from prompiler.backends import BackendAdapter
from prompiler.backends.mock import MockAdapter

AdapterFactory = Callable[[], BackendAdapter]

ADAPTER_FACTORIES: list[AdapterFactory] = [
    MockAdapter,
]


# A minimal but non-trivial schema: two required string fields plus one
# optional string. Real specs project to shapes like this after the P2
# degradation pass strips unsupported keywords.
HAPPY_PATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "reason": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}

HAPPY_PATH_PROMPT = "Classify the following text into one routing bucket."


@pytest.fixture(params=ADAPTER_FACTORIES, ids=lambda f: f.__name__)
def adapter(request: pytest.FixtureRequest) -> BackendAdapter:
    factory: AdapterFactory = request.param
    return factory()


@pytest.mark.unit
def test_adapter_satisfies_protocol(adapter: BackendAdapter) -> None:
    assert isinstance(adapter, BackendAdapter)


@pytest.mark.unit
def test_extract_returns_dict(adapter: BackendAdapter) -> None:
    result = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    assert isinstance(result, dict)


@pytest.mark.unit
def test_extract_contains_required_keys(adapter: BackendAdapter) -> None:
    result = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    for key in HAPPY_PATH_SCHEMA["required"]:
        assert key in result, f"required key {key!r} missing from {result!r}"


@pytest.mark.unit
def test_extract_is_deterministic(adapter: BackendAdapter) -> None:
    first = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    second = asyncio.run(adapter.extract(prompt=HAPPY_PATH_PROMPT, json_schema=HAPPY_PATH_SCHEMA))
    assert first == second
