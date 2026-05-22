"""MockAdapter — deterministic in-memory BackendAdapter for tests & goldens.

Why this exists, not just a stub:

  * The shared P2 contract test (``tests/test_backend_contract.py``) is
    parametrized over an ``ADAPTER_FACTORIES`` list. MockAdapter is the
    first factory and stays in that list forever — it's the reference
    implementation new adapters are diffed against during review.
  * P5 golden tests (``run_one`` + ``run_batch``) need a backend that
    returns byte-stable output without network access. Determinism is the
    point, not realism.

Strategy: for every key in ``json_schema['required']`` produce the literal
string ``"mock"``. This is intentionally trivial — the contract test only
checks presence of required keys plus equality between two calls. We do
not synthesize per-type values (e.g. ``0`` for ``"type": "integer"``)
because that would couple MockAdapter to the JSON Schema dialect, and the
moment we have a real adapter we'd rather feed those tests cassettes than
expand the mock.

No I/O, no randomness, no clock reads — same inputs always return an
equal dict.
"""

from __future__ import annotations

import copy
from typing import Any


class MockAdapter:
    """Returns ``{key: "mock"}`` for every key in ``json_schema['required']``.

    Satisfies the ``BackendAdapter`` Protocol structurally; the contract
    test verifies that via ``isinstance``.
    """

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        required = json_schema.get("required", [])
        return {key: "mock" for key in required}

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(json_schema)
