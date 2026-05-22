"""Pydantic model synthesiser — turn an EntitySpec into a runtime BaseModel (P1.5/P1.6).

Public entry: ``synthesize_model(spec) -> type[BaseModel]``.

Determinism: building the same spec twice yields models with equal JSON Schemas
(PLAN.md L107). Class names derive from ``spec.name`` (snake_case → PascalCase);
nested object models compose parent + child name to keep names unique.

Cross-field constraints (PLAN.md L97) compile into ``model_validator(mode="after")``
hooks attached to a dynamic base class — see ``prompiler.compiler.constraints``.

Recursion shape lives in ``prompiler.compiler.walk`` so static codegen and this
runtime emitter share traversal/naming and cannot drift.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from prompiler.compiler.constraints import check_constraints, compile_constraints
from prompiler.compiler.walk import pascal_case, walk_field
from prompiler.spec import EntitySpec, FieldSpec

_SCALAR_TYPE_MAP: dict[str, type[Any]] = {
    "string": str,
    "integer": int,
    "decimal": Decimal,
    "boolean": bool,
    "date": _dt.date,
    "datetime": _dt.datetime,
}

_NUMERIC_TYPES = frozenset({"integer", "decimal"})


class _DefaultBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


def synthesize_model(spec: EntitySpec) -> type[BaseModel]:
    """Build a fresh Pydantic model class from an EntitySpec."""
    allowed_fields = _top_level_field_names(spec)
    compiled = compile_constraints(spec.cross_field_constraints, allowed_fields)
    base = _make_constraint_base(compiled) if compiled else _DefaultBase
    class_name = pascal_case(spec.name)
    if spec.task == "extract":
        return _build_extract_model(class_name, spec.fields or [], base)
    return _build_classify_model(class_name, spec, base)


def _top_level_field_names(spec: EntitySpec) -> set[str]:
    if spec.task == "extract":
        return {f.name for f in (spec.fields or []) if f.name}
    if spec.allow_multi_label:
        return {"labels"}
    return {"label"}


def _make_constraint_base(
    compiled: list[tuple[str, str, Any]],
) -> type[BaseModel]:
    class _ConstraintBase(BaseModel):
        model_config = ConfigDict(extra="forbid")

        @model_validator(mode="after")
        def _check_cross_fields(self) -> _ConstraintBase:
            check_constraints(self, compiled)
            return self

    return _ConstraintBase


def _build_extract_model(
    class_name: str, fields: list[FieldSpec], base: type[BaseModel]
) -> type[BaseModel]:
    visitor = _TypeVisitor()
    field_defs: dict[str, tuple[Any, Any]] = {}
    for field in fields:
        if field.name is None:
            continue
        annotation = walk_field(field, class_name, visitor)
        field_defs[field.name] = _annotation_with_default(annotation, field.required)
    return _make_model(class_name, field_defs, base)


def _build_classify_model(
    class_name: str, spec: EntitySpec, base: type[BaseModel]
) -> type[BaseModel]:
    label_names = tuple(label.name for label in (spec.labels or []))
    literal: Any = Literal[label_names]
    field_defs: dict[str, tuple[Any, Any]]
    if spec.allow_multi_label:
        field_defs = {"labels": (cast(Any, list[literal]), ...)}
    else:
        field_defs = {"label": (literal, ...)}
    return _make_model(class_name, field_defs, base)


def _annotation_with_default(annotation: Any, required: bool) -> tuple[Any, Any]:
    if required:
        return annotation, ...
    return annotation | None, None


class _TypeVisitor:
    """Materialise Pydantic annotations. Nested object sub-models built eagerly
    via ``create_model`` inside ``visit_object`` — walker's bottom-up recursion
    means children are constructed before parent embeds them."""

    def visit_scalar(self, field: FieldSpec, parent: str) -> Any:
        base = _SCALAR_TYPE_MAP[field.type]
        constraint = _scalar_constraint(field)
        return Annotated[base, constraint] if constraint is not None else base

    def visit_enum(self, field: FieldSpec, parent: str) -> Any:
        return cast(Any, Literal[tuple(field.values or ())])

    def visit_array(self, field: FieldSpec, parent: str, inner: Any) -> Any:
        return cast(Any, list[inner])

    def visit_object(
        self,
        field: FieldSpec,
        parent: str,
        sub_name: str,
        children: list[tuple[FieldSpec, Any]],
    ) -> Any:
        field_defs: dict[str, tuple[Any, Any]] = {}
        for child, annotation in children:
            if child.name is None:
                continue
            field_defs[child.name] = _annotation_with_default(annotation, child.required)
        return _make_model(sub_name, field_defs, _DefaultBase)


def _scalar_constraint(field: FieldSpec) -> Any | None:
    if field.type == "string" and field.pattern is not None:
        return Field(pattern=field.pattern)
    if field.type in _NUMERIC_TYPES and (field.minimum is not None or field.maximum is not None):
        kwargs: dict[str, Any] = {}
        if field.minimum is not None:
            kwargs["ge"] = field.minimum
        if field.maximum is not None:
            kwargs["le"] = field.maximum
        return Field(**kwargs)
    return None


def _make_model(
    class_name: str,
    field_defs: dict[str, tuple[Any, Any]],
    base: type[BaseModel],
) -> type[BaseModel]:
    return create_model(  # type: ignore[call-overload, no-any-return]
        class_name,
        __base__=base,
        **field_defs,
    )
