"""YAML loader for EntitySpec with line/column error mapping (P1.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from prompiler.spec.model import EntitySpec

_LINE_KEY = "__line__"
_COL_KEY = "__column__"
_FILE_KEY = "__file__"
_MARK_KEYS = (_LINE_KEY, _COL_KEY, _FILE_KEY)


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
        return {k: _strip_marks(v) for k, v in value.items() if k not in _MARK_KEYS}
    if isinstance(value, list):
        return [_strip_marks(item) for item in value]
    return value


def _tag_file(value: Any, file: Path) -> Any:
    """Annotate every mapping in `value` with its originating `__file__`.

    Recurses into child values before stamping the dict so the file tag itself
    is never traversed. Lists are walked element-wise.
    """
    if isinstance(value, dict):
        for v in value.values():
            _tag_file(v, file)
        value[_FILE_KEY] = file
    elif isinstance(value, list):
        for item in value:
            _tag_file(item, file)
    return value


def _lookup_origin(raw: Any, loc: tuple[Any, ...]) -> tuple[int | None, int | None, Path | None]:
    """Walk `loc` from `raw`, returning the nearest mapping's line/column/file.

    Each level updates line/column/file from the deepest mapping that carries
    marks, so an error path resolves to the originating file even when the node
    was inherited from a parent spec via `extends`.
    """
    node: Any = raw
    line: int | None = None
    column: int | None = None
    file: Path | None = None
    if isinstance(node, dict):
        line = node.get(_LINE_KEY)
        column = node.get(_COL_KEY)
        file = node.get(_FILE_KEY)
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
            file = node.get(_FILE_KEY, file)
    return line, column, file


def _lookup_loc(raw: Any, loc: tuple[Any, ...]) -> tuple[int | None, int | None]:
    line, column, _ = _lookup_origin(raw, loc)
    return line, column


def _load_raw(p: Path) -> dict[str, Any]:
    """Read one spec file to a mark-annotated mapping; no `extends` resolution.

    Performs the IO/parse/root-shape/version-1 checks for a single file and
    returns the raw mapping with `__line__`/`__column__` marks intact.
    """
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

    version = raw.get("spec_version")
    if not isinstance(version, bool) and version == 1:
        loc_line, loc_col = _lookup_loc(raw, ("spec_version",))
        v1_line: int = loc_line if loc_line is not None else raw.get(_LINE_KEY, 1)
        v1_col: int = loc_col if loc_col is not None else raw.get(_COL_KEY, 0)
        raise SpecLoadError(
            file=p,
            message=(
                "spec_version 1 is no longer supported; "
                "run `prompiler migrate-spec` to upgrade this spec to version 2"
            ),
            line=v1_line,
            column=v1_col,
        )

    _tag_file(raw, p)
    return raw


def _resolve_parent_path(child_path: Path, extends_ref: str) -> Path:
    """Resolve a parent `extends` reference relative to the child's directory.

    Bare references gain a `.yaml` suffix; absolute references are used as-is.
    """
    ref = Path(extends_ref)
    if ref.suffix == "":
        ref = ref.with_suffix(".yaml")
    if ref.is_absolute():
        return ref
    return child_path.parent / ref


def _merge_named_list(parent_items: list[Any], child_items: list[Any]) -> list[Any]:
    """Merge two lists keyed by each item's `name`.

    A child item replaces the same-named parent item in the parent's position;
    unmatched child items are appended after; nameless items pass through.
    """
    child_by_name = {
        item["name"]: item
        for item in child_items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    consumed: set[str] = set()
    result: list[Any] = []
    for item in parent_items:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name in child_by_name:
            result.append(child_by_name[name])
            consumed.add(name)
        else:
            result.append(item)
    for item in child_items:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name in consumed:
            continue
        result.append(item)
    return result


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Overlay `child` onto `parent`, merging `fields`/`labels` by name."""
    merged = dict(parent)
    for key, value in child.items():
        existing = merged.get(key)
        if key in ("fields", "labels") and isinstance(value, list) and isinstance(existing, list):
            merged[key] = _merge_named_list(existing, value)
        else:
            merged[key] = value
    return merged


def _flatten(path: Path, raw: dict[str, Any], seen: frozenset[Path]) -> dict[str, Any]:
    """Recursively resolve `extends` into a single mark-free mapping.

    Detects inheritance cycles via resolved paths and strips the `extends` key
    from the result so the flattened spec is self-contained.
    """
    resolved = path.resolve()
    if resolved in seen:
        loc_line, loc_col = _lookup_loc(raw, ("extends",))
        c_line: int = loc_line if loc_line is not None else raw.get(_LINE_KEY, 1)
        c_col: int = loc_col if loc_col is not None else raw.get(_COL_KEY, 0)
        raise SpecLoadError(
            file=path,
            message=f"extends cycle detected at {path}",
            line=c_line,
            column=c_col,
        )

    extends_ref = raw.get("extends")
    if not isinstance(extends_ref, str) or not extends_ref.strip():
        return raw

    parent_path = _resolve_parent_path(path, extends_ref)
    parent_raw = _load_raw(parent_path)
    parent_flat = _flatten(parent_path, parent_raw, seen | {resolved})
    merged = _merge(parent_flat, raw)
    merged.pop("extends", None)
    return merged


def load_spec(path: Path | str) -> EntitySpec:
    """Load, flatten (resolve `extends`), and validate an EntitySpec.

    Raises SpecLoadError with `.file` / `.line` / `.column` populated when the
    file is missing, syntactically invalid, semantically invalid against the
    Pydantic model, has the wrong root shape, or has an unresolvable/cyclic
    `extends` chain. The returned spec is flattened: `extends` is resolved into
    one self-contained mapping before validation.
    """
    p = Path(path)
    raw = _load_raw(p)
    marked = _flatten(p, raw, frozenset())
    flattened = cast("dict[str, Any]", _strip_marks(marked))

    try:
        return EntitySpec.model_validate(flattened)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = tuple(err["loc"])
        loc_line, loc_col, loc_file = _lookup_origin(marked, loc)
        err_line: int = loc_line if loc_line is not None else marked.get(_LINE_KEY, 1)
        err_col: int = loc_col if loc_col is not None else marked.get(_COL_KEY, 0)
        err_file: Path = loc_file if loc_file is not None else p
        loc_path = ".".join(str(x) for x in loc) or "<root>"
        raise SpecLoadError(
            file=err_file,
            message=f"spec validation failed at {loc_path}: {err['msg']}",
            line=err_line,
            column=err_col,
        ) from exc
