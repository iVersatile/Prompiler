"""YAML fixture loader for the eval harness (P4).

A fixture file is a YAML *sequence* of evaluation cases (PRD FR-6):

    - name: simple_acme_invoice
      input: |
        ACME Corp ... total $1,234.56 USD ...
      expected:
        vendor_name: ACME Corp
        total_amount: "1234.56"
        currency: USD

Each case names an entity-extraction (or classification) scenario, the raw
``input`` document handed to the model, and the ``expected`` field/value map the
runner diffs against. The loader mirrors ``spec.loader``: it records YAML
line/column on every node and raises ``EvalError`` with ``file:line:col message``
diagnostics so a malformed fixture points at the offending case rather than the
whole file. ``EvalError`` is the sealed raise-target for "fixture parse / metric
computation" (runtime/errors.py, architecture.md L273) — this module composes on
it and does not invent a fixture-specific error class.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import yaml

from prompiler.runtime.errors import EvalError

_LINE_KEY = "__line__"
_COL_KEY = "__column__"


@dataclass(frozen=True)
class FixtureCase:
    """One evaluation case: a named input document plus its expected fields.

    ``expected`` is exposed as a read-only mapping so a loaded fixture cannot be
    mutated in place by a runner.
    """

    name: str
    input: str
    expected: Mapping[str, Any] = field(default_factory=dict)


class _LineMappingLoader(yaml.SafeLoader):
    """SafeLoader subclass that records source line/column on each mapping."""


def _construct_mapping(
    loader: _LineMappingLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, Any]:
    raw_mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    mapping = cast("dict[str, Any]", raw_mapping)
    mapping[_LINE_KEY] = node.start_mark.line + 1
    mapping[_COL_KEY] = node.start_mark.column
    return mapping


_LineMappingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _strip_marks(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_marks(v) for k, v in value.items() if k not in (_LINE_KEY, _COL_KEY)}
    if isinstance(value, list):
        return [_strip_marks(item) for item in value]
    return value


def _err(path: Path, message: str, line: int | None = None, column: int | None = None) -> EvalError:
    line_no = line if line is not None else 0
    col_no = column if column is not None else 0
    return EvalError(f"{path}:{line_no}:{col_no} {message}")


def load_fixtures(path: Path | str) -> tuple[FixtureCase, ...]:
    """Load and validate a fixture file into an immutable tuple of cases.

    Raises ``EvalError`` (with ``file:line:col`` diagnostics in the message) when
    the file is missing, unreadable, syntactically invalid, empty, not a YAML
    sequence, or contains a case with the wrong shape (missing/typed ``name`` /
    ``input`` / ``expected``).
    """
    p = Path(path)

    if not p.exists():
        raise _err(p, f"fixture file not found: {p}")

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise _err(p, f"cannot read fixture file: {exc}") from exc

    try:
        raw = yaml.load(text, Loader=_LineMappingLoader)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = (mark.line + 1) if mark is not None else 1
        col = mark.column if mark is not None else 0
        raise _err(p, f"YAML syntax error: {exc.problem or exc}", line, col) from exc
    except yaml.YAMLError as exc:
        raise _err(p, f"YAML error: {exc}", 1, 0) from exc

    if raw is None:
        raise _err(p, "fixture file is empty", 1, 0)
    if not isinstance(raw, list):
        raise _err(p, f"fixture root must be a sequence, got {type(raw).__name__}", 1, 0)

    cases: list[FixtureCase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        case = _build_case(p, index, item)
        if case.name in seen:
            dup_line: int | None = item.get(_LINE_KEY) if isinstance(item, dict) else None
            raise _err(p, f"case[{index}]: duplicate case name {case.name!r}", dup_line, 0)
        seen.add(case.name)
        cases.append(case)

    return tuple(cases)


def _build_case(path: Path, index: int, item: Any) -> FixtureCase:
    if not isinstance(item, dict):
        raise _err(path, f"case[{index}] must be a mapping, got {type(item).__name__}", 1, 0)

    line = item.get(_LINE_KEY)
    col = item.get(_COL_KEY, 0)

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _err(path, f"case[{index}]: 'name' must be a non-empty string", line, col)

    raw_input = item.get("input")
    if not isinstance(raw_input, str):
        type_name = "missing" if raw_input is None else type(raw_input).__name__
        raise _err(
            path, f"case[{index}] ({name}): 'input' must be a string, got {type_name}", line, col
        )

    expected = item.get("expected")
    if not isinstance(expected, dict):
        type_name = "missing" if expected is None else type(expected).__name__
        raise _err(
            path,
            f"case[{index}] ({name}): 'expected' must be a mapping, got {type_name}",
            line,
            col,
        )

    clean_expected = cast("dict[str, Any]", _strip_marks(expected))
    return FixtureCase(name=name, input=raw_input, expected=MappingProxyType(clean_expected))
