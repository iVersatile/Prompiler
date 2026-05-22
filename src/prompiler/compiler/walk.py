"""Shared FieldSpec visitor — recursion shape lives here so codegen and
runtime-model emitters share traversal/naming and cannot drift (P1.x)."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from prompiler.spec import FieldSpec

T = TypeVar("T")


@runtime_checkable
class FieldVisitor(Protocol[T]):
    """Emit a result per node. Walker pre-computes sub-class names and
    recursive results so visitors only describe the per-shape mapping."""

    def visit_scalar(self, field: FieldSpec, parent: str) -> T: ...
    def visit_enum(self, field: FieldSpec, parent: str) -> T: ...
    def visit_array(self, field: FieldSpec, parent: str, inner: T) -> T: ...
    def visit_object(
        self,
        field: FieldSpec,
        parent: str,
        sub_name: str,
        children: list[tuple[FieldSpec, T]],
    ) -> T: ...


def pascal_case(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def walk_field(field: FieldSpec, parent: str, visitor: FieldVisitor[T]) -> T:
    t = field.type
    if t == "array":
        assert field.item is not None
        inner = walk_field(field.item, parent, visitor)
        return visitor.visit_array(field, parent, inner)
    if t == "object":
        sub_name = parent + pascal_case(field.name or "Item")
        children = [(c, walk_field(c, sub_name, visitor)) for c in (field.fields or [])]
        return visitor.visit_object(field, parent, sub_name, children)
    if t == "enum":
        return visitor.visit_enum(field, parent)
    return visitor.visit_scalar(field, parent)


def walk_fields(
    fields: list[FieldSpec], parent: str, visitor: FieldVisitor[T]
) -> list[tuple[FieldSpec, T]]:
    return [(f, walk_field(f, parent, visitor)) for f in fields]
