"""Integration test: PRD §4 use case 3 — research paper citation extract.

Exercises three things the other integration tests don't:

- ``cite: true`` injects the citation directive into the synthesized prompt
  (prompt-only — no schema-level citation field) — the adapter receives it and
  we assert on the captured prompt string.
- ``required: false`` on a leaf field — null is accepted by the compiled model.
- nested ``array`` of ``object`` (authors) with its own scalar shape.

Adapter is fully scripted; no network, no API key.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

import pytest
from pydantic import BaseModel, ValidationError

from prompiler.backends.base import ExtractResult
from prompiler.compiler import compile_spec
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry
from prompiler.spec import EntitySpec

_RETRY_BUDGET: Final[int] = 2

_CITATION_SPEC: Final[dict[str, Any]] = {
    "spec_version": 1,
    "name": "paper_citation",
    "task": "extract",
    "cite": True,
    "fields": [
        {"name": "title", "type": "string", "required": True},
        {
            "name": "doi",
            "type": "string",
            "required": False,
            "pattern": r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$",
        },
        {"name": "year", "type": "integer", "required": False},
        {
            "name": "authors",
            "type": "array",
            "required": True,
            "item": {
                "type": "object",
                "fields": [
                    {"name": "given_name", "type": "string", "required": True},
                    {"name": "family_name", "type": "string", "required": True},
                    {"name": "orcid", "type": "string", "required": False},
                ],
            },
        },
    ],
}

_VALID_PAYLOAD: Final[dict[str, Any]] = {
    "title": "Attention Is All You Need",
    "doi": "10.48550/arXiv.1706.03762",
    "year": 2017,
    "authors": [
        {"given_name": "Ashish", "family_name": "Vaswani", "orcid": None},
        {"given_name": "Noam", "family_name": "Shazeer", "orcid": None},
    ],
}


class _ScriptedAdapter:
    def __init__(self, script: list[dict[str, Any] | Exception]) -> None:
        self._script: list[dict[str, Any] | Exception] = list(script)
        self.calls: int = 0
        self.last_prompt: str | None = None

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
    ) -> ExtractResult:
        self.calls += 1
        self.last_prompt = prompt
        head = self._script.pop(0)
        if isinstance(head, Exception):
            raise head
        return ExtractResult(data=head, system_fingerprint=None, deterministic=True)

    def supports(self, feature: str) -> bool:
        return feature == "seed"

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _register() -> Registry:
    registry = Registry()
    registry.register("paper_citation", compile_spec(EntitySpec.model_validate(_CITATION_SPEC)))
    return registry


@pytest.mark.integration
def test_citation_extract_happy_path_with_optional_fields_null() -> None:
    registry = _register()
    adapter = _ScriptedAdapter([_VALID_PAYLOAD])

    result = asyncio.run(run("paper_citation", "paper body", backend=adapter, registry=registry))

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.title == "Attention Is All You Need"
    assert result.doi == "10.48550/arXiv.1706.03762"
    assert result.year == 2017
    assert len(result.authors) == 2
    assert result.authors[0].family_name == "Vaswani"
    assert result.authors[0].orcid is None


@pytest.mark.integration
def test_citation_extract_accepts_payload_with_omitted_optional_fields() -> None:
    registry = _register()
    minimal = {
        "title": "Attention Is All You Need",
        "authors": [
            {"given_name": "Ashish", "family_name": "Vaswani"},
        ],
    }
    adapter = _ScriptedAdapter([minimal])

    result = asyncio.run(run("paper_citation", "paper body", backend=adapter, registry=registry))

    assert adapter.calls == 1
    assert isinstance(result, BaseModel)
    assert result.doi is None
    assert result.year is None
    assert result.authors[0].orcid is None


@pytest.mark.integration
def test_citation_extract_emits_citation_directive_in_prompt() -> None:
    registry = _register()
    adapter = _ScriptedAdapter([_VALID_PAYLOAD])

    asyncio.run(run("paper_citation", "paper body", backend=adapter, registry=registry))

    assert adapter.last_prompt is not None
    assert "## Citations" in adapter.last_prompt
    assert "include a citation" in adapter.last_prompt


@pytest.mark.integration
def test_citation_extract_rejects_doi_pattern_violation() -> None:
    registry = _register()
    bad = dict(_VALID_PAYLOAD, doi="not-a-doi")
    adapter = _ScriptedAdapter([bad] * _RETRY_BUDGET)

    with pytest.raises(ExtractionFailed) as exc_info:
        asyncio.run(run("paper_citation", "paper body", backend=adapter, registry=registry))

    assert adapter.calls == _RETRY_BUDGET
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValidationError)
    assert any("doi" in ".".join(str(p) for p in err["loc"]) for err in cause.errors())
