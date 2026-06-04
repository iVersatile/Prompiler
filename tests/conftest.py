"""Shared pytest fixtures and test helpers for the prompiler test suite.

This module is auto-loaded by pytest. Symbols defined here can be imported
from sibling test modules via ``from conftest import ...`` because pytest
inserts the conftest directory onto ``sys.path`` during collection.
"""

from __future__ import annotations

from typing import Any


class ScriptedAdapter:
    """Backend adapter that replays a queue of scripted responses.

    Each ``extract`` call pops the head of the queue:
    - a ``dict`` is returned as the adapter response;
    - an ``Exception`` is raised.

    Used by integration tests that exercise the orchestrator end-to-end
    against a deterministic adapter — no network, no API key.
    """

    def __init__(self, script: list[dict[str, Any] | Exception]) -> None:
        self._script: list[dict[str, Any] | Exception] = list(script)
        self.calls: int = 0

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        head = self._script.pop(0)
        if isinstance(head, Exception):
            raise head
        return head

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)
