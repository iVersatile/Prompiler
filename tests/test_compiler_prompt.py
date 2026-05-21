"""Unit tests for prompiler.compiler.prompt_synth — prompt synthesiser (P1.7).

Covers PLAN.md L98 scope (verbatim):
  "Prompt synthesiser: builds a prompt from spec description, field
  descriptions, cite flag, and few-shot block (initially empty)."

Contract anchored in architecture.md L197 (verbatim):
  "Prompt synthesiser. Template-driven (Jinja2 with ``autoescape=True``).
  Sections: role framing, task description, field-by-field instruction block,
  citation directive (if ``cite: true``), few-shot examples, output schema
  reminder. Template inputs are bounded — no spec-controlled template
  injection."

Six fixed section markers the implementation must emit (extract task):
  ``## Role`` -> ``## Task`` -> ``## Fields`` -> ``## Citations`` (only if
  ``cite=True``) -> ``## Examples`` -> ``## Output schema``.

For classify tasks, ``## Fields`` is replaced by ``## Labels``.

Determinism: PRD FR-2 — same spec + same prompiler version => byte-identical
prompt. Threat model S3 (architecture.md L404) — spec strings are typed
inputs, never Jinja sources; HTML-special chars survive intact (autoescape
guards downstream HTML rendering, not the prompt itself), and Jinja syntax
inside spec strings must not execute.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompiler.compiler import synthesize_prompt
from prompiler.spec import EntitySpec


def _load(data: dict[str, Any]) -> EntitySpec:
    return EntitySpec.model_validate(data)


def _extract_spec(**overrides: Any) -> EntitySpec:
    base: dict[str, Any] = {
        "spec_version": 1,
        "name": "invoice",
        "task": "extract",
        "description": "Extract invoice fields from a scanned receipt.",
        "fields": [
            {
                "name": "invoice_number",
                "type": "string",
                "required": True,
                "description": "Unique invoice identifier.",
            },
            {
                "name": "total_amount",
                "type": "decimal",
                "required": True,
                "description": "Total amount due in the invoice currency.",
            },
            {
                "name": "notes",
                "type": "string",
                "required": False,
                "description": "Optional free-form notes.",
            },
        ],
    }
    base.update(overrides)
    return _load(base)


def _classify_spec(**overrides: Any) -> EntitySpec:
    base: dict[str, Any] = {
        "spec_version": 1,
        "name": "email_category",
        "task": "classify",
        "description": "Classify customer email by intent.",
        "labels": [
            {"name": "billing", "description": "Billing or payment questions."},
            {"name": "support", "description": "Technical support requests."},
            {"name": "other", "description": "Anything else."},
        ],
    }
    base.update(overrides)
    return _load(base)


def _section_index(prompt: str, marker: str) -> int:
    idx = prompt.find(marker)
    assert idx >= 0, f"missing section {marker!r} in prompt:\n{prompt}"
    return idx


@pytest.mark.unit
def test_returns_str() -> None:
    prompt = synthesize_prompt(_extract_spec())
    assert isinstance(prompt, str)
    assert prompt.strip() != ""


@pytest.mark.unit
def test_extract_contains_all_six_section_markers_when_cite_true() -> None:
    prompt = synthesize_prompt(_extract_spec(cite=True))
    markers = (
        "## Role",
        "## Task",
        "## Fields",
        "## Citations",
        "## Examples",
        "## Output schema",
    )
    for marker in markers:
        assert marker in prompt, f"missing {marker!r}"


@pytest.mark.unit
def test_extract_omits_citations_section_when_cite_false() -> None:
    prompt = synthesize_prompt(_extract_spec(cite=False))
    assert "## Citations" not in prompt
    for marker in ("## Role", "## Task", "## Fields", "## Examples", "## Output schema"):
        assert marker in prompt


@pytest.mark.unit
def test_extract_section_ordering_with_cite() -> None:
    prompt = synthesize_prompt(_extract_spec(cite=True))
    order = [
        _section_index(prompt, "## Role"),
        _section_index(prompt, "## Task"),
        _section_index(prompt, "## Fields"),
        _section_index(prompt, "## Citations"),
        _section_index(prompt, "## Examples"),
        _section_index(prompt, "## Output schema"),
    ]
    assert order == sorted(order), f"sections out of order: {order}"


@pytest.mark.unit
def test_extract_section_ordering_without_cite() -> None:
    prompt = synthesize_prompt(_extract_spec(cite=False))
    order = [
        _section_index(prompt, "## Role"),
        _section_index(prompt, "## Task"),
        _section_index(prompt, "## Fields"),
        _section_index(prompt, "## Examples"),
        _section_index(prompt, "## Output schema"),
    ]
    assert order == sorted(order)


@pytest.mark.unit
def test_task_section_contains_description() -> None:
    prompt = synthesize_prompt(_extract_spec())
    assert "Extract invoice fields from a scanned receipt." in prompt


@pytest.mark.unit
def test_each_field_name_appears_in_fields_section() -> None:
    prompt = synthesize_prompt(_extract_spec())
    fields_start = _section_index(prompt, "## Fields")
    next_section = prompt.find("## ", fields_start + len("## Fields"))
    fields_block = prompt[fields_start:next_section] if next_section >= 0 else prompt[fields_start:]
    for name in ("invoice_number", "total_amount", "notes"):
        assert name in fields_block, f"field {name!r} missing from Fields section"


@pytest.mark.unit
def test_each_field_description_appears_in_fields_section() -> None:
    prompt = synthesize_prompt(_extract_spec())
    for desc in (
        "Unique invoice identifier.",
        "Total amount due in the invoice currency.",
        "Optional free-form notes.",
    ):
        assert desc in prompt


@pytest.mark.unit
def test_field_types_documented() -> None:
    prompt = synthesize_prompt(_extract_spec())
    assert "string" in prompt
    assert "decimal" in prompt


@pytest.mark.unit
def test_required_vs_optional_fields_distinguished() -> None:
    prompt = synthesize_prompt(_extract_spec()).lower()
    assert "required" in prompt
    assert "optional" in prompt


@pytest.mark.unit
def test_examples_section_present_even_when_empty() -> None:
    prompt = synthesize_prompt(_extract_spec())
    assert "## Examples" in prompt


@pytest.mark.unit
def test_output_schema_reminder_present() -> None:
    prompt = synthesize_prompt(_extract_spec())
    assert "## Output schema" in prompt
    schema_idx = _section_index(prompt, "## Output schema")
    tail = prompt[schema_idx:].lower()
    assert "json" in tail


@pytest.mark.unit
def test_classify_uses_labels_section_not_fields() -> None:
    prompt = synthesize_prompt(_classify_spec())
    assert "## Labels" in prompt
    assert "## Fields" not in prompt


@pytest.mark.unit
def test_classify_lists_each_label_name_and_description() -> None:
    prompt = synthesize_prompt(_classify_spec())
    for name in ("billing", "support", "other"):
        assert name in prompt
    for desc in (
        "Billing or payment questions.",
        "Technical support requests.",
        "Anything else.",
    ):
        assert desc in prompt


@pytest.mark.unit
def test_classify_single_label_directive() -> None:
    prompt = synthesize_prompt(_classify_spec(allow_multi_label=False)).lower()
    assert "exactly one" in prompt or "single label" in prompt


@pytest.mark.unit
def test_classify_multi_label_directive() -> None:
    prompt = synthesize_prompt(_classify_spec(allow_multi_label=True)).lower()
    assert "one or more" in prompt or "multiple labels" in prompt or "multi-label" in prompt


@pytest.mark.unit
def test_determinism_extract_byte_identical() -> None:
    spec = _extract_spec(cite=True)
    a = synthesize_prompt(spec)
    b = synthesize_prompt(spec)
    assert a == b


@pytest.mark.unit
def test_determinism_classify_byte_identical() -> None:
    spec = _classify_spec(allow_multi_label=True)
    a = synthesize_prompt(spec)
    b = synthesize_prompt(spec)
    assert a == b


@pytest.mark.unit
def test_jinja_syntax_in_description_does_not_execute() -> None:
    spec = _extract_spec(description="{{ 7 * 7 }} and {% raw %}{{ x }}{% endraw %}")
    prompt = synthesize_prompt(spec)
    assert "49" not in prompt
    assert "{{ 7 * 7 }}" in prompt or "{ 7 * 7 }" in prompt


@pytest.mark.unit
def test_jinja_syntax_in_field_description_does_not_execute() -> None:
    spec = _extract_spec(
        fields=[
            {
                "name": "x",
                "type": "string",
                "description": "{{ 1+1 }} and {% for i in range(3) %}{{ i }}{% endfor %}",
            }
        ]
    )
    prompt = synthesize_prompt(spec)
    assert "2 and 012" not in prompt
    assert "{{ 1+1 }}" in prompt or "{ 1+1 }" in prompt


@pytest.mark.unit
def test_jinja_syntax_in_label_description_does_not_execute() -> None:
    spec = _classify_spec(
        labels=[
            {"name": "evil", "description": "{{ 9*9 }}"},
            {"name": "ok", "description": "fine"},
        ]
    )
    prompt = synthesize_prompt(spec)
    assert "81" not in prompt


@pytest.mark.unit
def test_html_special_chars_in_description_preserved() -> None:
    """Spec strings flow into a prompt (plain text), not HTML output. Whether the
    prompt stores them raw or HTML-escaped, the original text must be recoverable
    and the literals must not vanish."""
    spec = _extract_spec(description="A & B < C > D")
    prompt = synthesize_prompt(spec)
    raw_present = "A & B < C > D" in prompt
    escaped_present = "A &amp; B &lt; C &gt; D" in prompt
    assert raw_present or escaped_present, prompt


@pytest.mark.unit
def test_no_extract_specific_sections_in_classify_prompt() -> None:
    prompt = synthesize_prompt(_classify_spec())
    assert "invoice_number" not in prompt
    assert "## Fields" not in prompt


@pytest.mark.unit
def test_role_section_first() -> None:
    prompt = synthesize_prompt(_extract_spec())
    role_idx = _section_index(prompt, "## Role")
    assert prompt[:role_idx].strip() == "" or role_idx == 0 or prompt[:role_idx].count("## ") == 0
