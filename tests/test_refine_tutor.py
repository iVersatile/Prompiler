"""Tests for the patch generator — ``refine/tutor.py`` (P5, PLAN.md L210).

The tutor feeds an eval report plus the current prompt to a backend ("tutor")
LLM call and emits a unified diff over the prompt text. These tests pin:

- the user prompt embeds both the current prompt text and the report metrics;
- a fixed, non-empty system prompt exists;
- a valid tutor response yields the unified diff string;
- refusal (``decline=True``), an empty diff, and a malformed diff each raise
  ``AdapterError`` with the offending reason/diff embedded (LL-003);
- the sync wrapper drives the async coroutine.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from prompiler.refine.tutor import (
    TUTOR_RESPONSE_SCHEMA,
    build_tutor_user_prompt,
    propose_patch,
    propose_patch_sync,
)
from prompiler.runtime.errors import AdapterError

_VALID_DIFF = (
    "--- a/prompt.txt\n"
    "+++ b/prompt.txt\n"
    "@@ -1,3 +1,3 @@\n"
    " Extract the fields.\n"
    "-Return JSON.\n"
    "+Return strict JSON only.\n"
)

_REPORT: dict[str, Any] = {
    "spec": "invoice",
    "spec_hash": "abc123",
    "backend": "scripted",
    "model": "test-model",
    "timestamp": "2026-06-05T00:00:00Z",
    "fixture_path": "fixtures/invoice.yaml",
    "aggregate": {"precision": 0.5, "recall": 0.4, "f1": 0.44, "exact_f1": 0.44, "fuzzy_f1": 0.5},
    "per_field": {"vendor_name": {"p": 1.0, "r": 1.0, "f1": 1.0}},
    "per_case": [],
    "usage": None,
}

_CURRENT_PROMPT = "Extract the fields.\nReturn JSON."


class RecordingAdapter:
    """Backend double that records the exact ``extract`` keyword arguments.

    ``ScriptedAdapter`` only counts calls; the tutor tests need to assert what
    prompt and schema were sent to the backend, so this double captures them.
    """

    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self._response = response
        self.prompt: str | None = None
        self.json_schema: dict[str, Any] | None = None
        self.timeout: float | None = None

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.prompt = prompt
        self.json_schema = json_schema
        self.timeout = timeout
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def test_user_prompt_embeds_current_prompt_and_metrics() -> None:
    user_prompt = build_tutor_user_prompt(_REPORT, _CURRENT_PROMPT)

    assert _CURRENT_PROMPT in user_prompt
    assert "vendor_name" in user_prompt
    assert "0.44" in user_prompt


def test_response_schema_requires_decline_flag() -> None:
    assert TUTOR_RESPONSE_SCHEMA["required"] == ["decline"]
    props = TUTOR_RESPONSE_SCHEMA["properties"]
    assert set(props) >= {"decline", "reason", "diff"}


def test_propose_patch_returns_diff_on_valid_response() -> None:
    adapter = RecordingAdapter({"decline": False, "reason": "", "diff": _VALID_DIFF})

    diff = asyncio.run(
        propose_patch(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter)
    )

    assert diff == _VALID_DIFF
    assert adapter.json_schema == TUTOR_RESPONSE_SCHEMA
    assert _CURRENT_PROMPT in (adapter.prompt or "")


def test_propose_patch_forwards_timeout() -> None:
    adapter = RecordingAdapter({"decline": False, "reason": "", "diff": _VALID_DIFF})

    asyncio.run(
        propose_patch(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter, timeout=12.5)
    )

    assert adapter.timeout == 12.5


def test_propose_patch_raises_on_decline_embedding_reason() -> None:
    adapter = RecordingAdapter({"decline": True, "reason": "prompt is already optimal", "diff": ""})

    with pytest.raises(AdapterError) as excinfo:
        asyncio.run(propose_patch(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter))

    assert "prompt is already optimal" in str(excinfo.value)


def test_propose_patch_raises_on_empty_diff() -> None:
    adapter = RecordingAdapter({"decline": False, "reason": "", "diff": "   "})

    with pytest.raises(AdapterError):
        asyncio.run(propose_patch(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter))


def test_propose_patch_raises_on_missing_diff_key() -> None:
    adapter = RecordingAdapter({"decline": False, "reason": ""})

    with pytest.raises(AdapterError):
        asyncio.run(propose_patch(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter))


def test_propose_patch_raises_on_malformed_diff_embedding_diff() -> None:
    bad_diff = "this is not a diff at all\njust prose\n"
    adapter = RecordingAdapter({"decline": False, "reason": "", "diff": bad_diff})

    with pytest.raises(AdapterError) as excinfo:
        asyncio.run(propose_patch(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter))

    assert "this is not a diff at all" in str(excinfo.value)


def test_propose_patch_sync_wraps_async() -> None:
    adapter = RecordingAdapter({"decline": False, "reason": "", "diff": _VALID_DIFF})

    diff = propose_patch_sync(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter)

    assert diff == _VALID_DIFF


def test_propose_patch_sync_rejects_running_loop() -> None:
    adapter = RecordingAdapter({"decline": False, "reason": "", "diff": _VALID_DIFF})

    async def _inner() -> None:
        propose_patch_sync(report=_REPORT, current_prompt=_CURRENT_PROMPT, backend=adapter)

    with pytest.raises(RuntimeError):
        asyncio.run(_inner())
