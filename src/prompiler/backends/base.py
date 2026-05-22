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

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BackendAdapter(Protocol):
    """Vendor-agnostic JSON extraction contract.

    Implementations take a natural-language prompt plus a JSON Schema
    describing the expected response shape and return a dict that conforms
    to that schema's ``required`` keys at minimum.
    """

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]: ...

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
