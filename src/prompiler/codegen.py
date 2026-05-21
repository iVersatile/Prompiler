"""Static-codegen path — render a spec to a standalone Python module (P1.11).

Mirrors the dynamic ``pydantic.create_model`` synthesis in
``prompiler.compiler.model`` as emitted source strings. The generated file
imports only stdlib + ``pydantic`` so downstream projects can vendor it
into their own repo for IDE autocomplete and offline imports without a
``prompiler`` runtime dependency (PLAN.md L102).

Determinism: the same ``EntitySpec`` always renders byte-identical source.
``COMPILER_PROTOCOL_VERSION`` and ``SPEC_HASH`` are pinned as module-level
literals so drift between a vendored copy and the live spec is detectable.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from prompiler import COMPILER_PROTOCOL_VERSION
from prompiler.spec import EntitySpec, FieldSpec, spec_hash

_SCALAR_ANNOTATION: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "decimal": "Decimal",
    "boolean": "bool",
    "date": "_dt.date",
    "datetime": "_dt.datetime",
}

_NUMERIC_TYPES = frozenset({"integer", "decimal"})


def render(spec: EntitySpec) -> str:
    """Render ``spec`` to a standalone Python module source string."""
    body = _build_body(spec)
    template_text = (
        resources.files("prompiler.templates")
        .joinpath("compiled.py.j2")
        .read_text(encoding="utf-8")
    )
    env = Environment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return env.from_string(template_text).render(
        protocol_version=COMPILER_PROTOCOL_VERSION,
        spec_hash=spec_hash(spec),
        body=body,
    )


def write(spec: EntitySpec, out_dir: Path) -> Path:
    """Write the rendered module to ``<out_dir>/<spec.name>.py`` and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.name}.py"
    path.write_text(render(spec), encoding="utf-8")
    return path


def _build_body(spec: EntitySpec) -> str:
    class_name = _pascal_case(spec.name)
    classes: list[str] = []
    class_names: list[str] = []
    if spec.task == "extract":
        _emit_extract_class(class_name, spec.fields or [], classes, class_names)
    else:
        _emit_classify_class(class_name, spec, classes, class_names)
    body = "\n\n\n".join(classes)
    rebuilds = "\n".join(
        f"{name}.model_rebuild(_types_namespace=globals())" for name in class_names
    )
    return f"{body}\n\n\n{rebuilds}"


def _emit_extract_class(
    class_name: str,
    fields: list[FieldSpec],
    classes: list[str],
    class_names: list[str],
) -> None:
    field_lines: list[str] = []
    for field in fields:
        if field.name is None:
            continue
        annotation = _annotation_for(field, class_name, classes, class_names)
        if not field.required:
            annotation = f"{annotation} | None"
            default = "None"
        else:
            default = "..."
        field_lines.append(f"    {field.name}: {annotation} = {default}")
    classes.append(_class_block(class_name, field_lines))
    class_names.append(class_name)


def _emit_classify_class(
    class_name: str,
    spec: EntitySpec,
    classes: list[str],
    class_names: list[str],
) -> None:
    label_names = tuple(label.name for label in (spec.labels or []))
    literal = _literal_expr(label_names)
    if spec.allow_multi_label:
        field_lines = [f"    labels: list[{literal}] = ..."]
    else:
        field_lines = [f"    label: {literal} = ..."]
    classes.append(_class_block(class_name, field_lines))
    class_names.append(class_name)


def _class_block(class_name: str, field_lines: list[str]) -> str:
    if not field_lines:
        field_lines = ["    pass"]
    header = f'class {class_name}(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n'
    return header + "\n".join(field_lines)


def _annotation_for(
    field: FieldSpec,
    parent_name: str,
    classes: list[str],
    class_names: list[str],
) -> str:
    t = field.type
    if t in _SCALAR_ANNOTATION:
        base = _SCALAR_ANNOTATION[t]
        constraint = _scalar_constraint(field)
        if constraint is None:
            return base
        return f"Annotated[{base}, {constraint}]"
    if t == "enum":
        return _literal_expr(tuple(field.values or ()))
    if t == "array":
        assert field.item is not None
        inner = _annotation_for(field.item, parent_name, classes, class_names)
        return f"list[{inner}]"
    if t == "object":
        sub_name = parent_name + _pascal_case(field.name or "Item")
        _emit_extract_class(sub_name, field.fields or [], classes, class_names)
        return sub_name
    raise ValueError(f"unsupported field type: {t!r}")


def _scalar_constraint(field: FieldSpec) -> str | None:
    if field.type == "string" and field.pattern is not None:
        return f"Field(pattern={field.pattern!r})"
    if field.type in _NUMERIC_TYPES and (field.minimum is not None or field.maximum is not None):
        parts: list[str] = []
        if field.minimum is not None:
            parts.append(f"ge={field.minimum!r}")
        if field.maximum is not None:
            parts.append(f"le={field.maximum!r}")
        return f"Field({', '.join(parts)})"
    return None


def _literal_expr(values: tuple[str, ...]) -> str:
    rendered = ", ".join(repr(v) for v in values)
    return f"Literal[{rendered}]"


def _pascal_case(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)
