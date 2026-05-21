"""YAML loader for EntitySpec with line/column error mapping (P1.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from prompiler.spec.model import EntitySpec

_LINE_KEY = "__line__"
_COL_KEY = "__column__"


class SpecLoadError(Exception):
    """Raised when a spec file cannot be loaded or fails validation.

    Attributes carry the originating file plus the YAML line/column closest to
    the offending node so callers can render `file:line:col message` diagnostics.
    """

    def __init__(
        self,
        *,
        file: Path,
        message: str,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.file = file
        self.message = message
        self.line = line
        self.column = column

    def __str__(self) -> str:
        line = self.line if self.line is not None else 0
        col = self.column if self.column is not None else 0
        return f"{self.file}:{line}:{col} {self.message}"


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


def _lookup_loc(raw: Any, loc: tuple[Any, ...]) -> tuple[int | None, int | None]:
    node: Any = raw
    line: int | None = None
    column: int | None = None
    if isinstance(node, dict):
        line = node.get(_LINE_KEY)
        column = node.get(_COL_KEY)
    for key in loc:
        if isinstance(node, dict) and isinstance(key, str) and key in node:  # noqa: SIM114
            node = node[key]
        elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        else:
            break
        if isinstance(node, dict):
            line = node.get(_LINE_KEY, line)
            column = node.get(_COL_KEY, column)
    return line, column


def load_spec(path: Path | str) -> EntitySpec:
    """Load and validate an EntitySpec from a YAML file.

    Raises SpecLoadError with `.file` / `.line` / `.column` populated when the
    file is missing, syntactically invalid, semantically invalid against the
    Pydantic model, or has the wrong root shape.
    """
    p = Path(path)

    if not p.exists():
        raise SpecLoadError(file=p, message=f"spec file not found: {p}")

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecLoadError(file=p, message=f"cannot read spec file: {exc}") from exc

    try:
        raw = yaml.load(text, Loader=_LineMappingLoader)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = (mark.line + 1) if mark is not None else 1
        col = mark.column if mark is not None else 0
        problem = exc.problem or str(exc)
        raise SpecLoadError(
            file=p,
            message=f"YAML syntax error: {problem}",
            line=line,
            column=col,
        ) from exc
    except yaml.YAMLError as exc:
        raise SpecLoadError(file=p, message=f"YAML error: {exc}", line=1, column=0) from exc

    if raw is None:
        raise SpecLoadError(file=p, message="spec file is empty", line=1, column=0)
    if not isinstance(raw, dict):
        raise SpecLoadError(
            file=p,
            message=f"spec root must be a mapping, got {type(raw).__name__}",
            line=1,
            column=0,
        )

    clean = _strip_marks(raw)

    try:
        return EntitySpec.model_validate(clean)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = tuple(err["loc"])
        loc_line, loc_col = _lookup_loc(raw, loc)
        err_line: int = loc_line if loc_line is not None else raw.get(_LINE_KEY, 1)
        err_col: int = loc_col if loc_col is not None else raw.get(_COL_KEY, 0)
        loc_path = ".".join(str(x) for x in loc) or "<root>"
        raise SpecLoadError(
            file=p,
            message=f"spec validation failed at {loc_path}: {err['msg']}",
            line=err_line,
            column=err_col,
        ) from exc
