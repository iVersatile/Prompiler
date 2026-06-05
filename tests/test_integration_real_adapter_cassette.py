"""Integration: a real backend adapter driven through ``orchestrator.run`` (G3).

The existing integration tier (``test_integration_backend_swap.py``) drives the
orchestrator with *scripted dict doubles* only — it never exercises a concrete
``BackendAdapter``. The contract tier (``test_backend_contract.py``) exercises
the real adapters against cassettes, but in *isolation* — it never feeds them
through ``orchestrator.run``. Neither tier proves a concrete adapter survives an
orchestrator round-trip end to end.

This module closes that gap: a cassette-backed real adapter (claude / openai /
gemini) is registered against a compiled spec and driven through
``orchestrator.run``, asserting the decoded tool output reaches the caller as a
validated pydantic model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Final

import httpx
import pytest
from pydantic import BaseModel

from _cassette import CassettePlayer
from _cassette_transport import make_cassette_transport
from prompiler.backends import BackendAdapter, ClaudeAdapter, GeminiAdapter, OpenAIAdapter
from prompiler.backends.claude import ANTHROPIC_BASE_URL
from prompiler.backends.gemini import GEMINI_BASE_URL
from prompiler.backends.openai import OPENAI_BASE_URL
from prompiler.compiler import compile_spec
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_CASSETTES: Final[Path] = Path(__file__).parent / "cassettes"

_ROUTING_SPEC: Final[dict[str, object]] = {
    "spec_version": 1,
    "name": "routing",
    "task": "extract",
    "fields": [
        {"name": "label", "type": "string", "required": True},
        {"name": "reason", "type": "string", "required": True},
    ],
}

_EXPECTED: Final[dict[str, str]] = {
    "label": "support",
    "reason": "user requests human assistance",
}


def _claude_factory() -> BackendAdapter:
    player = CassettePlayer.from_json((_CASSETTES / "claude_happy_path.json").read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=ANTHROPIC_BASE_URL)
    return ClaudeAdapter(client=client)


def _openai_factory() -> BackendAdapter:
    player = CassettePlayer.from_json((_CASSETTES / "openai_happy_path.json").read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=OPENAI_BASE_URL)
    return OpenAIAdapter(client=client)


def _gemini_factory() -> BackendAdapter:
    player = CassettePlayer.from_json((_CASSETTES / "gemini_happy_path.json").read_text())
    transport = make_cassette_transport(player)
    client = httpx.AsyncClient(transport=transport, base_url=GEMINI_BASE_URL)
    return GeminiAdapter(client=client)


def _register() -> Registry:
    registry = Registry()
    registry.register("routing", compile_spec(EntitySpec.model_validate(_ROUTING_SPEC)))
    return registry


@pytest.mark.integration
@pytest.mark.parametrize(
    "factory",
    [_claude_factory, _openai_factory, _gemini_factory],
    ids=["claude", "openai", "gemini"],
)
def test_real_adapter_drives_orchestrator_from_cassette(
    factory: Callable[[], BackendAdapter],
) -> None:
    registry = _register()
    adapter = factory()

    result = asyncio.run(
        run("routing", "Classify the following text.", backend=adapter, registry=registry)
    )

    assert isinstance(result, BaseModel)
    assert result.model_dump() == _EXPECTED
