"""Shared backend plumbing for the runnable scenario clients.

Each sibling client (``invoice_client.py``, ``medical_letter_client.py``,
``receipt_client.py``) is a self-contained walkthrough of using prompiler
against a realistic document. They share two things, kept here so the three
scripts do not drift:

1. ``ScriptedBackend`` — a hermetic ``BackendAdapter`` that replays a fixed
   payload. This is the default so every client runs on a CPU-only host with
   no API keys (CI-safe, deterministic).
2. ``select_backend`` — opt-in switch to a real vendor adapter when the
   operator exports ``PROMPILER_LIVE=1`` plus the matching provider key. This
   is the path a real product would take; it stays off by default.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from prompiler.backends import BackendAdapter, ClaudeAdapter
from prompiler.backends.base import ExtractResult, ModalContent


class ScriptedBackend:
    """Replays a fixed list of payloads. Hermetic stand-in for a live vendor.

    A ``BackendAdapter`` is any object with an async ``extract`` and a
    ``to_tool_schema``. The orchestrator pops one payload per call, so passing
    more than one payload exercises the corrective-retry loop.
    """

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.calls = 0

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
        return ExtractResult(
            data=self._payloads.pop(0), system_fingerprint=None, deterministic=True
        )

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def select_backend(*scripted_payloads: dict[str, Any]) -> BackendAdapter:
    """Return a live adapter when opted in, else a hermetic ScriptedBackend.

    Live path requires both ``PROMPILER_LIVE=1`` and ``ANTHROPIC_API_KEY``.
    Without those the script stays fully reproducible on the scripted payload,
    which is what CI and the golden snapshots rely on.
    """
    if os.environ.get("PROMPILER_LIVE") == "1":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            return ClaudeAdapter(api_key=api_key)
        raise SystemExit(
            "PROMPILER_LIVE=1 set but ANTHROPIC_API_KEY is missing; "
            "export the key or unset PROMPILER_LIVE to use the scripted backend."
        )
    return ScriptedBackend(*scripted_payloads)
