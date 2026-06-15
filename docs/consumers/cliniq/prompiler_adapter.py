"""DRAFT — cliniq→prompiler backend shim (not wired into any import path).

Wraps a cliniq ``LLMAdapter`` so it satisfies prompiler's ``BackendAdapter``
Protocol, letting cliniq feed its *existing* adapter straight into
``prompiler.runtime.run_sync``. This is the **thin-shim** adoption path (Option
B in README.md): minimal blast radius, but degraded fidelity — see README for
the cost table and the Option A (native prompiler backend) end-state.

Fidelity costs baked in here:
  * sync→async via ``asyncio.to_thread`` (cliniq's adapter is blocking);
  * prompiler hands ONE assembled prompt string — it lands on cliniq's
    ``system`` slot with ``user=""``;
  * ``deterministic`` is always False (cliniq adapters expose no honoured seed);
  * ``supports`` is always False (no seed / multimodal / streaming guarantees);
  * ``to_tool_schema`` is identity (deep copy) — cliniq adapters accept full
    JSON Schema, so no dialect projection is attempted.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Sequence
from typing import Any

from cliniq.adapters.base import LLMAdapter

from prompiler.backends.base import ExtractResult, ModalContent


class PrompilerBackendAdapter:
    """Adapt a cliniq ``LLMAdapter`` to prompiler's ``BackendAdapter`` Protocol."""

    def __init__(self, inner: LLMAdapter) -> None:
        self._inner = inner

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
        if modal_parts:
            raise NotImplementedError("cliniq LLMAdapter is text-only")
        data = await asyncio.to_thread(
            self._inner.complete_json,
            system=prompt,
            user="",
            schema=json_schema,
        )
        return ExtractResult(data=data, system_fingerprint=None, deterministic=False)

    def supports(self, feature: str) -> bool:
        return False

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)
