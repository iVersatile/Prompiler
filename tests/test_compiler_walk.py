"""Tests for ``prompiler.compiler.walk`` — shared FieldSpec visitor protocol."""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pytest

from prompiler.compiler.walk import FieldVisitor, pascal_case, walk_field, walk_fields
from prompiler.spec import FieldSpec


@dataclass
class _Call:
    kind: str
    field_name: str | None
    parent: str
    extra: str = ""


@dataclass
class RecordingVisitor:
    """Visitor test double: returns a deterministic tag string per visit."""

    calls: list[_Call] = dc_field(default_factory=list)

    def visit_scalar(self, field: FieldSpec, parent: str) -> str:
        self.calls.append(_Call("scalar", field.name, parent))
        return f"scalar:{field.type}:{field.name}"

    def visit_enum(self, field: FieldSpec, parent: str) -> str:
        values = tuple(field.values or ())
        self.calls.append(_Call("enum", field.name, parent, extra=repr(values)))
        return f"enum:{field.name}:{values}"

    def visit_array(self, field: FieldSpec, parent: str, inner: str) -> str:
        self.calls.append(_Call("array", field.name, parent, extra=inner))
        return f"array[{inner}]"

    def visit_object(
        self,
        field: FieldSpec,
        parent: str,
        sub_name: str,
        children: list[tuple[FieldSpec, str]],
    ) -> str:
        self.calls.append(_Call("object", field.name, parent, extra=sub_name))
        return f"object:{sub_name}"


@pytest.mark.unit
def test_pascal_case_snake_to_pascal() -> None:
    assert pascal_case("vendor_name") == "VendorName"


@pytest.mark.unit
def test_pascal_case_single_word() -> None:
    assert pascal_case("invoice") == "Invoice"


@pytest.mark.unit
def test_pascal_case_skips_empty_parts() -> None:
    assert pascal_case("__foo__bar__") == "FooBar"


@pytest.mark.unit
def test_walk_field_scalar_invokes_visit_scalar() -> None:
    visitor = RecordingVisitor()
    f = FieldSpec(name="amount", type="integer")
    result = walk_field(f, "Invoice", visitor)
    assert result == "scalar:integer:amount"
    assert [c.kind for c in visitor.calls] == ["scalar"]
    assert visitor.calls[0].parent == "Invoice"


@pytest.mark.unit
def test_walk_field_enum_invokes_visit_enum() -> None:
    visitor = RecordingVisitor()
    f = FieldSpec(name="currency", type="enum", values=["USD", "EUR"])
    result = walk_field(f, "Invoice", visitor)
    assert result == "enum:currency:('USD', 'EUR')"
    assert [c.kind for c in visitor.calls] == ["enum"]


@pytest.mark.unit
def test_walk_field_array_walks_inner_first() -> None:
    visitor = RecordingVisitor()
    inner = FieldSpec(name=None, type="string")
    f = FieldSpec(name="tags", type="array", item=inner)
    result = walk_field(f, "Invoice", visitor)
    assert result == "array[scalar:string:None]"
    assert [c.kind for c in visitor.calls] == ["scalar", "array"]


@pytest.mark.unit
def test_walk_field_object_pre_computes_sub_name() -> None:
    visitor = RecordingVisitor()
    sku = FieldSpec(name="sku", type="string")
    qty = FieldSpec(name="qty", type="integer")
    line_item = FieldSpec(name="line_item", type="object", fields=[sku, qty])
    result = walk_field(line_item, "Invoice", visitor)
    assert result == "object:InvoiceLineItem"
    object_call = visitor.calls[-1]
    assert object_call.kind == "object"
    assert object_call.extra == "InvoiceLineItem"


@pytest.mark.unit
def test_walk_field_unnamed_object_defaults_to_item() -> None:
    visitor = RecordingVisitor()
    sku = FieldSpec(name="sku", type="string")
    unnamed_obj = FieldSpec(name=None, type="object", fields=[sku])
    arr = FieldSpec(name="items", type="array", item=unnamed_obj)
    walk_field(arr, "Invoice", visitor)
    object_call = next(c for c in visitor.calls if c.kind == "object")
    assert object_call.extra == "InvoiceItem"


@pytest.mark.unit
def test_walk_field_nested_array_of_objects() -> None:
    visitor = RecordingVisitor()
    sku = FieldSpec(name="sku", type="string")
    qty = FieldSpec(name="qty", type="integer")
    obj = FieldSpec(name=None, type="object", fields=[sku, qty])
    arr = FieldSpec(name="items", type="array", item=obj)
    result = walk_field(arr, "Invoice", visitor)
    assert result == "array[object:InvoiceItem]"
    assert [c.kind for c in visitor.calls] == ["scalar", "scalar", "object", "array"]


@pytest.mark.unit
def test_walk_fields_preserves_order_and_pairs_results() -> None:
    visitor = RecordingVisitor()
    a = FieldSpec(name="a", type="string")
    b = FieldSpec(name="b", type="integer")
    pairs = walk_fields([a, b], "Invoice", visitor)
    assert [f.name for f, _ in pairs] == ["a", "b"]
    assert [r for _, r in pairs] == ["scalar:string:a", "scalar:integer:b"]


@pytest.mark.unit
def test_field_visitor_is_runtime_protocol() -> None:
    visitor: FieldVisitor[str] = RecordingVisitor()
    assert isinstance(visitor, FieldVisitor)
