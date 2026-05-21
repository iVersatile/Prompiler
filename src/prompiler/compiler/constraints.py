"""Cross-field constraint compiler (P1.6).

Compiles ``EntitySpec.cross_field_constraints`` entries into runtime validators
attached to the synthesised Pydantic model. Expressions are evaluated by an
AST-whitelist interpreter (no ``eval``), so spec authors cannot smuggle arbitrary
Python in via constraint text.

Supported expression surface:
  - Comparisons: ==, !=, <, <=, >, >=
  - Boolean: and, or, not
  - Arithmetic: + - * / // %
  - Whitelisted calls: sum, len, min, max, abs
  - Dot-path field references with list projection across array-of-object fields,
    e.g. ``sum(line_items.line_total)`` projects to ``[item.line_total for item in line_items]``.

Severity dispatch (per ``Constraint.severity``):
  - ``error``: raise on violation -> surfaces as ``ValidationError``
  - ``warn``:  ``warnings.warn`` on violation, instance still validates
"""

from __future__ import annotations

import ast
import operator
import warnings
from collections.abc import Callable
from typing import Any

from prompiler.spec import Constraint

_ALLOWED_FUNCS: dict[str, Callable[..., Any]] = {
    "sum": sum,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
}

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_CMPOPS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def compile_constraints(
    constraints: list[Constraint], allowed_fields: set[str]
) -> list[tuple[str, str, Callable[[Any], Any]]]:
    """Compile each constraint into ``(expr_src, severity, evaluator)``.

    Raises ``ValueError`` at compile time for syntax errors, disallowed AST
    nodes/functions, or unknown top-level field references.
    """
    return [(c.expr, c.severity, _compile_one(c.expr, allowed_fields)) for c in constraints]


def check_constraints(instance: Any, compiled: list[tuple[str, str, Callable[[Any], Any]]]) -> None:
    """Evaluate compiled constraints against an instance; raise/warn on violation."""
    for expr_src, severity, evaluator in compiled:
        if not evaluator(instance):
            msg = f"cross-field constraint violated: {expr_src!r}"
            if severity == "error":
                raise ValueError(msg)
            warnings.warn(msg, stacklevel=3)


def _compile_one(expr_src: str, allowed_fields: set[str]) -> Callable[[Any], Any]:
    try:
        tree = ast.parse(expr_src, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid constraint expression {expr_src!r}: {exc}") from exc
    _validate(tree.body, allowed_fields, expr_src)
    return lambda instance: _eval(tree.body, instance)


def _validate(node: ast.AST, allowed_fields: set[str], expr_src: str) -> None:
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise ValueError(f"disallowed boolean operator in {expr_src!r}")
        for v in node.values:
            _validate(v, allowed_fields, expr_src)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not):
            raise ValueError(f"disallowed unary operator in {expr_src!r}")
        _validate(node.operand, allowed_fields, expr_src)
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ValueError(f"disallowed binary operator in {expr_src!r}")
        _validate(node.left, allowed_fields, expr_src)
        _validate(node.right, allowed_fields, expr_src)
        return
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _CMPOPS:
                raise ValueError(f"disallowed comparison operator in {expr_src!r}")
        _validate(node.left, allowed_fields, expr_src)
        for c in node.comparators:
            _validate(c, allowed_fields, expr_src)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError(f"disallowed function call in {expr_src!r}")
        if node.keywords:
            raise ValueError(f"keyword arguments not allowed in {expr_src!r}")
        for a in node.args:
            _validate(a, allowed_fields, expr_src)
        return
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError(f"dunder/private attribute access not allowed in {expr_src!r}")
        _validate(node.value, allowed_fields, expr_src)
        return
    if isinstance(node, ast.Name):
        if node.id not in allowed_fields:
            raise ValueError(f"unknown field {node.id!r} in {expr_src!r}")
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, str, bool)) and node.value is not None:
            raise ValueError(f"disallowed constant in {expr_src!r}")
        return
    raise ValueError(f"disallowed expression node {type(node).__name__} in {expr_src!r}")


def _eval(node: ast.AST, instance: Any) -> Any:
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in node.values:
                result = _eval(v, instance)
                if not result:
                    return result
            return result
        result = False
        for v in node.values:
            result = _eval(v, instance)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        return not _eval(node.operand, instance)
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, instance), _eval(node.right, instance))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, instance)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, instance)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        fn = _ALLOWED_FUNCS[node.func.id]
        args = [_eval(a, instance) for a in node.args]
        return fn(*args)
    if isinstance(node, (ast.Attribute, ast.Name)):
        path = _attribute_path(node)
        return _resolve_path(instance, path)
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError(f"unsupported AST node at eval time: {type(node).__name__}")


def _attribute_path(node: ast.AST) -> list[str]:
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    assert isinstance(cur, ast.Name)
    parts.append(cur.id)
    parts.reverse()
    return parts


def _resolve_path(root: Any, path: list[str]) -> Any:
    value: Any = getattr(root, path[0])
    for part in path[1:]:
        if isinstance(value, list):
            value = [_resolve_path_step(item, part) for item in value]
        else:
            value = getattr(value, part)
    return value


def _resolve_path_step(value: Any, attr: str) -> Any:
    if isinstance(value, list):
        return [_resolve_path_step(item, attr) for item in value]
    return getattr(value, attr)
