"""Prompt synthesiser (P1.7).

Renders a deterministic, byte-identical prompt from an ``EntitySpec``.

Contract anchored in architecture.md L197 (verbatim):
  "Prompt synthesiser. Template-driven (Jinja2 with ``autoescape=True``).
  Sections: role framing, task description, field-by-field instruction block,
  citation directive (if ``cite: true``), few-shot examples, output schema
  reminder. Template inputs are bounded — no spec-controlled template
  injection."

Spec strings (description, field/label descriptions) flow into the template
as variable values, not as template source — Jinja syntax inside them is
inert. Autoescape protects downstream HTML renderers; the prompt text itself
may contain HTML entities, which is acceptable for an LLM input.
"""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined

from prompiler.spec import EntitySpec, FieldSpec, Label

_ENV = Environment(
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)

_TEMPLATE_SRC = """\
## Role
You are a precise information extraction assistant. Follow the instructions exactly and return only the requested structured output.

## Task
{{ description or "(no description provided)" }}

{% if task == "extract" %}
## Fields
Extract the following fields from the input. Each field lists its name, type, and whether it is required or optional.

{% for f in fields %}
- **{{ f.name }}** (type: {{ f.type }}, {{ "required" if f.required else "optional" }}): {{ f.description or "(no description)" }}
{% endfor %}
{% else %}
## Labels
{% if allow_multi_label %}
Assign one or more labels (multi-label classification) from the list below.
{% else %}
Assign exactly one label (single label classification) from the list below.
{% endif %}

{% for l in labels %}
- **{{ l.name }}**: {{ l.description or "(no description)" }}
{% endfor %}
{% endif %}

{% if cite %}
## Citations
For every extracted value, include a citation pointing to the supporting span in the source text. Do not invent citations; if a value cannot be cited, mark it as missing.

{% endif %}
## Examples
(No examples provided.)

## Output schema
Return a single JSON object that conforms to the supplied JSON Schema. Do not include any prose, markdown, or commentary outside the JSON.
"""

_TEMPLATE = _ENV.from_string(_TEMPLATE_SRC)


def synthesize_prompt(spec: EntitySpec) -> str:
    """Render the prompt for ``spec``. Pure function — deterministic per spec."""
    fields: list[FieldSpec] = spec.fields or []
    labels: list[Label] = spec.labels or []
    return _TEMPLATE.render(
        description=spec.description,
        task=spec.task,
        cite=spec.cite,
        allow_multi_label=spec.allow_multi_label,
        fields=fields,
        labels=labels,
    )
