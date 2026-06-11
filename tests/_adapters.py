"""Shared scripted backend adapters for integration tests.

These duck-type the ``BackendAdapter`` protocol (see
``prompiler.backends.base``) without any network or API key. Two shapes cover
every integration scenario:

- ``ScriptedAdapter`` — a single FIFO queue of payloads/exceptions, replayed in
  order. Records every prompt it sees (``.prompts``) and exposes the most recent
  via ``.last_prompt``. Asserts on exhaustion so a test scripting fewer
  responses than the orchestrator requests fails loudly instead of an opaque
  ``IndexError``.
- ``SchemaDispatchAdapter`` — keyed FIFO queues dispatched by the schema's
  property set (``frozenset(json_schema["properties"].keys())``). One instance
  serves multiple distinct specs in a single ``asyncio.gather`` batch without
  leaking cursor state between them. An unknown schema raises ``KeyError``.

Both accept ``Exception`` instances inline in their queues; an exception is
raised (not returned) when popped, to inject retry/failure scenarios.

Imported top-level as ``from _adapters import ...`` — ``tests/`` is not a
package (no ``__init__.py``) and pytest runs in default "prepend" import mode,
so ``tests/`` is on ``sys.path``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from prompiler.backends.base import ExtractResult, ModalContent


class ScriptedAdapter:
    """Replay a single FIFO queue of payloads/exceptions, recording prompts."""

    def __init__(self, script: list[dict[str, Any] | Exception]) -> None:
        self._script: list[dict[str, Any] | Exception] = list(script)
        self.calls: int = 0
        self.prompts: list[str] = []

    @property
    def last_prompt(self) -> str | None:
        return self.prompts[-1] if self.prompts else None

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
        self.calls += 1
        self.prompts.append(prompt)
        assert self._script, (
            f"ScriptedAdapter exhausted on call {self.calls}; "
            "test scripted fewer responses than orchestrator requested"
        )
        head = self._script.pop(0)
        if isinstance(head, Exception):
            raise head
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


class SchemaDispatchAdapter:
    """Dispatch by the schema's property set across per-spec FIFO queues.

    A single instance serves multiple distinct specs in one batch. Per-spec
    queues isolate state: popping for spec A never advances spec B's cursor.
    Queues accept payload dicts *or* ``Exception`` instances (raised on pop).
    An unregistered schema raises ``KeyError``.
    """

    def __init__(self, queues: dict[frozenset[str], list[dict[str, Any] | Exception]]) -> None:
        self._queues: dict[frozenset[str], list[dict[str, Any] | Exception]] = {
            key: list(items) for key, items in queues.items()
        }
        self.calls: int = 0

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
        self.calls += 1
        key = frozenset(json_schema["properties"].keys())
        head = self._queues[key].pop(0)
        if isinstance(head, Exception):
            raise head
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)
