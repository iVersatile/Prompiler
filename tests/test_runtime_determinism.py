"""Determinism surface tests — PLAN.md P9.1 (L328-389).

Covers the gaps that ``test_backend_contract.py`` does not:

- Seed lands in the *wire payload* for the two seed-honouring adapters
  (Ollama nests it under ``options``; OpenAI puts it top-level), and is
  omitted entirely when ``seed=None`` while ``temperature`` always rides.
- The orchestrator emits a TRACE record tagged ``event="deterministic"``
  carrying the ``deterministic``/``system_fingerprint`` flags per call.
- OpenAI surfaces ``system_fingerprint`` only when it is a string.
- FR-2: temperature config precedence (kwarg > env > pyproject > default)
  threads through ``run`` / ``run_sync`` / ``run_batch``.
- FR-14: exactly one WARN per non-seed backend per process.

pytest-asyncio is not installed; coroutines are driven via ``asyncio.run``
inside sync test functions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from prompiler import obs
from prompiler.backends.base import ExtractResult, ModalContent
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter
from prompiler.compiler import compile_spec
from prompiler.runtime import orchestrator
from prompiler.runtime.orchestrator import run, run_batch, run_sync
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
    "additionalProperties": False,
}

_OMIT = object()


class _CapturingTransport:
    """Records the JSON body of every request, replies with a canned body."""

    def __init__(self, response_json: dict[str, Any]) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._response_json = response_json

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.bodies.append(json.loads(request.content))
            return httpx.Response(200, json=self._response_json)

        return httpx.MockTransport(handler)


def _ollama_client(capture: _CapturingTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://localhost:11434", transport=capture.transport())


def _openai_client(capture: _CapturingTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.openai.com", transport=capture.transport())


def _ollama_response() -> dict[str, Any]:
    return {"message": {"content": json.dumps({"label": "ok"})}}


def _openai_response(*, system_fingerprint: Any = "fp_abc") -> dict[str, Any]:
    body: dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "extract",
                                "arguments": json.dumps({"label": "ok"}),
                            }
                        }
                    ]
                }
            }
        ]
    }
    if system_fingerprint is not _OMIT:
        body["system_fingerprint"] = system_fingerprint
    return body


# ----- (a) seed lands in the wire payload (and is omitted when None) -----


@pytest.mark.unit
def test_ollama_seed_lands_in_options_payload() -> None:
    capture = _CapturingTransport(_ollama_response())
    adapter = OllamaAdapter(client=_ollama_client(capture))

    asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, temperature=0.0, seed=42))

    assert str(capture.requests[0].url) == "http://localhost:11434/api/chat"
    options = capture.bodies[0]["options"]
    assert options["seed"] == 42
    assert options["temperature"] == 0.0


@pytest.mark.unit
def test_ollama_omits_seed_when_none_but_keeps_temperature() -> None:
    capture = _CapturingTransport(_ollama_response())
    adapter = OllamaAdapter(client=_ollama_client(capture))

    asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, temperature=0.3, seed=None))

    options = capture.bodies[0]["options"]
    assert "seed" not in options
    assert options["temperature"] == 0.3


@pytest.mark.unit
def test_openai_seed_lands_top_level_in_payload() -> None:
    capture = _CapturingTransport(_openai_response())
    adapter = OpenAIAdapter(client=_openai_client(capture))

    asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, temperature=0.0, seed=7))

    assert str(capture.requests[0].url) == "https://api.openai.com/v1/chat/completions"
    body = capture.bodies[0]
    assert body["seed"] == 7
    assert body["temperature"] == 0.0


@pytest.mark.unit
def test_openai_omits_seed_when_none_but_keeps_temperature() -> None:
    capture = _CapturingTransport(_openai_response())
    adapter = OpenAIAdapter(client=_openai_client(capture))

    asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, temperature=0.5, seed=None))

    body = capture.bodies[0]
    assert "seed" not in body
    assert body["temperature"] == 0.5


# ----- (c) OpenAI system_fingerprint surfacing (str only) -----


@pytest.mark.unit
def test_openai_surfaces_string_system_fingerprint() -> None:
    capture = _CapturingTransport(_openai_response(system_fingerprint="fp_xyz"))
    adapter = OpenAIAdapter(client=_openai_client(capture))

    result = asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, seed=1))

    assert result.system_fingerprint == "fp_xyz"
    assert result.deterministic is True


@pytest.mark.unit
def test_openai_drops_non_string_system_fingerprint() -> None:
    capture = _CapturingTransport(_openai_response(system_fingerprint=12345))
    adapter = OpenAIAdapter(client=_openai_client(capture))

    result = asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, seed=1))

    assert result.system_fingerprint is None


@pytest.mark.unit
def test_openai_absent_system_fingerprint_surfaces_none() -> None:
    capture = _CapturingTransport(_openai_response(system_fingerprint=_OMIT))
    adapter = OpenAIAdapter(client=_openai_client(capture))

    result = asyncio.run(adapter.extract(prompt="p", json_schema=_SCHEMA, seed=1))

    assert result.system_fingerprint is None


# ----- orchestrator helpers -----


def _register(name: str) -> Registry:
    spec = EntitySpec.model_validate(
        {
            "spec_version": 2,
            "name": name,
            "task": "extract",
            "fields": [{"name": "title", "type": "string", "required": True}],
        }
    )
    registry = Registry()
    registry.register(name, compile_spec(spec))
    return registry


class _CapturingScriptedAdapter:
    """Replays one dict response; records the temperature/seed it was called with."""

    def __init__(
        self,
        *,
        supports_seed: bool = True,
        system_fingerprint: str | None = None,
        deterministic: bool = True,
    ) -> None:
        self._supports_seed = supports_seed
        self._fingerprint = system_fingerprint
        self._deterministic = deterministic
        self.temperatures: list[float] = []
        self.seeds: list[int | None] = []

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        modal_parts: Sequence[ModalContent] = (),
    ) -> ExtractResult:
        self.temperatures.append(temperature)
        self.seeds.append(seed)
        return ExtractResult(
            data={"title": "ok"},
            system_fingerprint=self._fingerprint,
            deterministic=self._deterministic,
        )

    def supports(self, feature: str) -> bool:
        return self._supports_seed and feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


# ----- (b) trace-tagging -----


@pytest.mark.unit
def test_orchestrator_emits_deterministic_trace_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter(system_fingerprint="fp_1", deterministic=True)

    with caplog.at_level(obs.TRACE, logger="prompiler.orchestrator"):
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))

    trace_records = [r for r in caplog.records if getattr(r, "event", None) == "deterministic"]
    assert trace_records, "expected an event=deterministic TRACE record"
    rec = trace_records[0]
    assert rec.levelno == obs.TRACE
    assert rec.deterministic is True  # type: ignore[attr-defined]
    assert rec.system_fingerprint == "fp_1"  # type: ignore[attr-defined]


# ----- (d) FR-2 temperature precedence: kwarg > env > pyproject > default -----


@pytest.mark.unit
def test_temperature_kwarg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_TEMPERATURE", "0.9")
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter()
    asyncio.run(run("doc", "body", backend=adapter, registry=registry, temperature=0.1))
    assert adapter.temperatures == [0.1]


@pytest.mark.unit
def test_temperature_env_overrides_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPILER_TEMPERATURE", "0.7")
    monkeypatch.setattr(orchestrator, "_read_prompiler_block", lambda: {"temperature": 0.2})
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter()
    asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    assert adapter.temperatures == [0.7]


@pytest.mark.unit
def test_temperature_pyproject_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_TEMPERATURE", raising=False)
    monkeypatch.setattr(orchestrator, "_read_prompiler_block", lambda: {"temperature": 0.4})
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter()
    asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    assert adapter.temperatures == [0.4]


@pytest.mark.unit
def test_temperature_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPILER_TEMPERATURE", raising=False)
    monkeypatch.setattr(orchestrator, "_read_prompiler_block", lambda: {})
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter()
    asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    assert adapter.temperatures == [0.0]


@pytest.mark.unit
def test_temperature_precedence_threads_through_sync_and_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMPILER_TEMPERATURE", "0.6")
    registry = _register("doc")
    sync_adapter = _CapturingScriptedAdapter()
    run_sync("doc", "body", backend=sync_adapter, registry=registry)
    assert sync_adapter.temperatures == [0.6]
    batch_adapter = _CapturingScriptedAdapter()
    asyncio.run(
        run_batch("doc", ["a", "b"], backend=batch_adapter, registry=registry, concurrency=2)
    )
    assert batch_adapter.temperatures == [0.6, 0.6]


# ----- (e) FR-14: one WARN per non-seed backend per process -----


@pytest.mark.unit
def test_nondeterministic_backend_warns_once_per_process(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator, "_warned_backends", set())
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter(supports_seed=False, deterministic=False)
    with caplog.at_level(logging.WARNING, logger="prompiler.orchestrator"):
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    warns = [r for r in caplog.records if getattr(r, "event", None) == "nondeterministic_backend"]
    assert len(warns) == 1
    assert warns[0].backend == "_CapturingScriptedAdapter"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_seed_backend_does_not_warn(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator, "_warned_backends", set())
    registry = _register("doc")
    adapter = _CapturingScriptedAdapter(supports_seed=True)
    with caplog.at_level(logging.WARNING, logger="prompiler.orchestrator"):
        asyncio.run(run("doc", "body", backend=adapter, registry=registry))
    warns = [r for r in caplog.records if getattr(r, "event", None) == "nondeterministic_backend"]
    assert warns == []
