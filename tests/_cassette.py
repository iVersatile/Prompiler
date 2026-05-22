"""Cassette recorder/player for paid backend determinism.

Pure stdlib. No httpx dependency — per-adapter transport shims bridge to
the player when adapters land. Bodies are base64-encoded so JSON cassettes
survive binary payloads. Header redaction strips credentials per RULES.md
section 8 before any cassette can be committed.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

REDACTED_HEADERS: frozenset[str] = frozenset(
    {"authorization", "x-api-key", "x-goog-api-key", "cookie", "set-cookie"}
)

REDACTED_VALUE = "***REDACTED***"


class CassetteExhausted(RuntimeError):
    """Player ran out of recorded interactions."""


@dataclass(frozen=True)
class CassetteInteraction:
    method: str
    url: str
    request_headers: dict[str, str]
    request_body: bytes
    status: int
    response_headers: dict[str, str]
    response_body: bytes


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: (REDACTED_VALUE if k.lower() in REDACTED_HEADERS else v) for k, v in headers.items()}


def serialize_interaction(interaction: CassetteInteraction) -> dict[str, Any]:
    return {
        "method": interaction.method,
        "url": interaction.url,
        "request_headers": redact_headers(interaction.request_headers),
        "request_body_b64": base64.b64encode(interaction.request_body).decode("ascii"),
        "status": interaction.status,
        "response_headers": redact_headers(interaction.response_headers),
        "response_body_b64": base64.b64encode(interaction.response_body).decode("ascii"),
    }


def deserialize_interaction(raw: dict[str, Any]) -> CassetteInteraction:
    return CassetteInteraction(
        method=raw["method"],
        url=raw["url"],
        request_headers=dict(raw["request_headers"]),
        request_body=base64.b64decode(raw["request_body_b64"]),
        status=raw["status"],
        response_headers=dict(raw["response_headers"]),
        response_body=base64.b64decode(raw["response_body_b64"]),
    )


class CassetteRecorder:
    """Accumulates interactions and serializes to a JSON string."""

    def __init__(self) -> None:
        self._interactions: list[CassetteInteraction] = []

    def record(self, interaction: CassetteInteraction) -> None:
        self._interactions.append(interaction)

    def to_json(self) -> str:
        payload = {
            "version": 1,
            "interactions": [serialize_interaction(i) for i in self._interactions],
        }
        return json.dumps(payload, indent=2, sort_keys=True)


class CassettePlayer:
    """Ordered, exhaustive replay. Overdraw raises CassetteExhausted."""

    def __init__(self, interactions: tuple[CassetteInteraction, ...]) -> None:
        self._interactions = interactions
        self._cursor = 0

    @classmethod
    def from_json(cls, raw: str) -> CassettePlayer:
        payload = json.loads(raw)
        items = tuple(deserialize_interaction(i) for i in payload["interactions"])
        return cls(items)

    def next(self) -> CassetteInteraction:
        if self._cursor >= len(self._interactions):
            raise CassetteExhausted(
                f"cassette has {len(self._interactions)} interactions; "
                f"requested index {self._cursor}"
            )
        item = self._interactions[self._cursor]
        self._cursor += 1
        return item

    @property
    def remaining(self) -> int:
        return len(self._interactions) - self._cursor
