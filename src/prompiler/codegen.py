"""Static-codegen path — render a spec to a standalone Python module (P1.11).

Mirrors the dynamic ``pydantic.create_model`` synthesis in
``prompiler.compiler.model`` as emitted source strings. The generated file
imports only stdlib + ``pydantic`` so downstream projects can vendor it
into their own repo for IDE autocomplete and offline imports without a
``prompiler`` runtime dependency (PLAN.md L102).

Determinism: the same ``EntitySpec`` always renders byte-identical source.
``COMPILER_PROTOCOL_VERSION`` and ``SPEC_HASH`` are pinned as module-level
literals so drift between a vendored copy and the live spec is detectable.

Recursion shape is delegated to ``prompiler.compiler.walk`` so the runtime
``create_model`` emitter cannot drift in traversal or sub-class naming.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from prompiler import COMPILER_PROTOCOL_VERSION
from prompiler.compiler.walk import pascal_case, walk_field
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
    class_name = pascal_case(spec.name)
    visitor = _SourceVisitor()
    if spec.task == "extract":
        _emit_extract_class(class_name, spec.fields or [], visitor)
    else:
        _emit_classify_class(class_name, spec, visitor)
    body = "\n\n\n".join(visitor.classes)
    rebuilds = "\n".join(
        f"{name}.model_rebuild(_types_namespace=globals())" for name in visitor.class_names
    )
    return f"{body}\n\n\n{rebuilds}"


def _emit_extract_class(
    class_name: str,
    fields: list[FieldSpec],
    visitor: _SourceVisitor,
) -> None:
    field_lines: list[str] = []
    for field in fields:
        if field.name is None:
            continue
        annotation = walk_field(field, class_name, visitor)
        field_lines.append(_field_line(field.name, annotation, field.required))
    visitor.classes.append(_class_block(class_name, field_lines))
    visitor.class_names.append(class_name)


def _emit_classify_class(
    class_name: str,
    spec: EntitySpec,
    visitor: _SourceVisitor,
) -> None:
    label_names = tuple(label.name for label in (spec.labels or []))
    literal = _literal_expr(label_names)
    if spec.allow_multi_label:
        field_lines = [f"    labels: list[{literal}] = ..."]
    else:
        field_lines = [f"    label: {literal} = ..."]
    visitor.classes.append(_class_block(class_name, field_lines))
    visitor.class_names.append(class_name)


def _field_line(name: str, annotation: str, required: bool) -> str:
    if required:
        return f"    {name}: {annotation} = ..."
    return f"    {name}: {annotation} | None = None"


def _class_block(class_name: str, field_lines: list[str]) -> str:
    if not field_lines:
        field_lines = ["    pass"]
    header = f'class {class_name}(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n'
    return header + "\n".join(field_lines)


class _SourceVisitor:
    """Emit annotation source strings. Sub-class blocks registered as side
    effect inside ``visit_object`` so bottom-up emission order matches the
    pre-walker recursive emitter (innermost first, top class last)."""

    def __init__(self) -> None:
        self.classes: list[str] = []
        self.class_names: list[str] = []

    def visit_scalar(self, field: FieldSpec, parent: str) -> str:
        base = _SCALAR_ANNOTATION[field.type]
        constraint = _scalar_constraint(field)
        if constraint is None:
            return base
        return f"Annotated[{base}, {constraint}]"

    def visit_enum(self, field: FieldSpec, parent: str) -> str:
        return _literal_expr(tuple(field.values or ()))

    def visit_array(self, field: FieldSpec, parent: str, inner: str) -> str:
        return f"list[{inner}]"

    def visit_object(
        self,
        field: FieldSpec,
        parent: str,
        sub_name: str,
        children: list[tuple[FieldSpec, str]],
    ) -> str:
        field_lines: list[str] = []
        for child, annotation in children:
            if child.name is None:
                continue
            field_lines.append(_field_line(child.name, annotation, child.required))
        self.classes.append(_class_block(sub_name, field_lines))
        self.class_names.append(sub_name)
        return sub_name


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
