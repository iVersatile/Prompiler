"""Pydantic model synthesiser — turn an EntitySpec into a runtime BaseModel (P1.5).

Public entry: ``synthesize_model(spec) -> type[BaseModel]``.

Determinism: building the same spec twice yields models with equal JSON Schemas
(PLAN.md L107). Class names derive from ``spec.name`` (snake_case → PascalCase);
nested object models compose parent + child name to keep names unique.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

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


def synthesize_model(spec: EntitySpec) -> type[BaseModel]:
    """Build a fresh Pydantic model class from an EntitySpec."""
    class_name = _pascal_case(spec.name)
    if spec.task == "extract":
        return _build_extract_model(class_name, spec.fields or [])
    return _build_classify_model(class_name, spec)


def _build_extract_model(class_name: str, fields: list[FieldSpec]) -> type[BaseModel]:
    field_defs: dict[str, tuple[Any, Any]] = {}
    for field in fields:
        if field.name is None:
            continue
        annotation, default = _field_annotation_and_default(field, class_name)
        field_defs[field.name] = (annotation, default)
    return _make_model(class_name, field_defs)


def _build_classify_model(class_name: str, spec: EntitySpec) -> type[BaseModel]:
    label_names = tuple(label.name for label in (spec.labels or []))
    literal: Any = Literal[label_names]
    field_defs: dict[str, tuple[Any, Any]]
    if spec.allow_multi_label:
        field_defs = {"labels": (cast(Any, list[literal]), ...)}
    else:
        field_defs = {"label": (literal, ...)}
    return _make_model(class_name, field_defs)


def _field_annotation_and_default(field: FieldSpec, parent_name: str) -> tuple[Any, Any]:
    annotation = _annotation_for(field, parent_name)
    if not field.required:
        return annotation | None, None
    return annotation, ...


def _annotation_for(field: FieldSpec, parent_name: str) -> Any:
    t = field.type
    if t in _SCALAR_TYPE_MAP:
        base = _SCALAR_TYPE_MAP[t]
        constraint = _scalar_constraint(field)
        return Annotated[base, constraint] if constraint is not None else base
    if t == "enum":
        return cast(Any, Literal[tuple(field.values or ())])
    if t == "array":
        assert field.item is not None  # guaranteed by FieldSpec validator
        inner: Any = _annotation_for(field.item, parent_name)
        return cast(Any, list[inner])
    if t == "object":
        sub_name = parent_name + _pascal_case(field.name or "Item")
        return _build_extract_model(sub_name, field.fields or [])
    raise ValueError(f"unsupported field type: {t!r}")


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


def _make_model(class_name: str, field_defs: dict[str, tuple[Any, Any]]) -> type[BaseModel]:
    return create_model(  # type: ignore[call-overload, no-any-return]
        class_name,
        __config__=ConfigDict(extra="forbid"),
        **field_defs,
    )


def _pascal_case(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)
