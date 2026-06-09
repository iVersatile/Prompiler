"""BackendAdapter Protocol — the contract every vendor adapter satisfies.

Source of truth: docs/PLAN.md §P2 acceptance:

  > All 4 adapters pass a shared 'happy-path extract' contract test.

The shape is async-from-day-1 (option B in the P2.1 design lock):

  * Real adapters (claude / openai / gemini / ollama) are HTTP-bound and have
    no synchronous SDK path worth preserving; making the Protocol sync would
    just force every implementation into ``asyncio.to_thread`` boilerplate.
  * P3's ``run_batch`` will fan extracts out via ``asyncio.Semaphore``; that
    only composes cleanly if ``extract`` is already a coroutine.

Keyword-only arguments keep call sites legible at adapter boundaries
(``adapter.extract(prompt=..., json_schema=...)``) and prevent silent
positional swaps when a fifth argument is added later (timeout, cassette
mode, etc.).

``@runtime_checkable`` is required by the shared contract test, which calls
``isinstance(adapter, BackendAdapter)`` to ensure new adapters at least
expose the right method name at the Protocol level. Note: this checks
attribute presence only, not the async signature — the other contract
tests cover behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of a single ``extract`` call plus determinism provenance.

    ``data`` is the schema-conforming payload (what callers used to receive
    directly as a dict). The extra fields surface run-time determinism so the
    orchestrator can trace-tag results and warn when a backend cannot honour a
    seed:

      * ``system_fingerprint`` — OpenAI returns this to identify the backend
        config that produced the output; other vendors leave it ``None``.
      * ``deterministic`` — True when the call was made with a seed the backend
        actually honours (FR-14 seed matrix); False for temperature-only
        backends (Claude, Gemini) that cannot guarantee reproducibility.

    Frozen so equality is structural — two identical extracts compare equal,
    preserving the determinism contract test.
    """

    data: dict[str, Any]
    system_fingerprint: str | None
    deterministic: bool


@runtime_checkable
class BackendAdapter(Protocol):
    """Vendor-agnostic JSON extraction contract.

    Implementations take a natural-language prompt plus a JSON Schema
    describing the expected response shape and return an ``ExtractResult``
    whose ``data`` conforms to that schema's ``required`` keys at minimum.
    """

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
    ) -> ExtractResult: ...

    def supports(self, feature: str) -> bool:
        """Report whether this backend honours a determinism ``feature``.

        The only feature queried today is ``"seed"``: Ollama and OpenAI honour
        a request seed (reproducible given fixed inputs); Claude and Gemini are
        temperature-only and return False. Unknown features return False.
        """
        ...

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        """Project ``json_schema`` into the dialect this backend accepts.

        Most backends (Claude, OpenAI, Ollama) accept full JSON Schema and
        return a deep copy. Gemini's schema dialect is OpenAPI-3-derived and
        rejects ``pattern``, ``format``, ``additionalProperties``,
        ``$schema``/``$id``/``$ref``/``$defs``, ``multipleOf``, and nesting
        deeper than five levels; ``GeminiAdapter`` strips those.

        The returned dict is always a fresh structure — callers may mutate
        it without aliasing the input.
        """
        ...
