"""Semantic linter over EntitySpec (P1.4).

Catches the four PLAN.md L108 categories that survive past type validation:
duplicate-field-name (sibling-scoped), duplicate-label-name,
missing-description (entity/field/label, recursive), and reserved-name
(Python keywords + small Pydantic-reserved set).
"""

from __future__ import annotations

import keyword
from dataclasses import dataclass

from prompiler.spec.model import EntitySpec, FieldSpec, Label

_PYDANTIC_RESERVED: frozenset[str] = frozenset(
    {
        "model_config",
        "model_fields",
        "model_computed_fields",
        "model_extra",
        "model_fields_set",
    }
)


@dataclass(frozen=True)
class LintIssue:
    path: str
    code: str
    message: str


def lint_spec(spec: EntitySpec) -> list[LintIssue]:
    issues: list[LintIssue] = []

    if not _has_text(spec.description):
        issues.append(
            LintIssue(
                path="description",
                code="missing-description",
                message=f"entity {spec.name!r} has no description",
            )
        )

    if spec.fields is not None:
        _lint_fields(spec.fields, path_prefix="fields", issues=issues)

    if spec.labels is not None:
        _lint_labels(spec.labels, issues=issues)

    return issues


def _lint_fields(fields: list[FieldSpec], *, path_prefix: str, issues: list[LintIssue]) -> None:
    seen: set[str] = set()
    for idx, field in enumerate(fields):
        path = f"{path_prefix}[{idx}]"
        name = field.name

        if name is not None:
            if name in seen:
                issues.append(
                    LintIssue(
                        path=path,
                        code="duplicate-field-name",
                        message=f"duplicate field name {name!r} at {path_prefix}",
                    )
                )
            else:
                seen.add(name)

            if _is_reserved(name):
                issues.append(
                    LintIssue(
                        path=path,
                        code="reserved-name",
                        message=f"field name {name!r} is reserved",
                    )
                )

            if not _has_text(field.description):
                issues.append(
                    LintIssue(
                        path=path,
                        code="missing-description",
                        message=f"field {name!r} has no description",
                    )
                )

        if field.fields is not None:
            _lint_fields(field.fields, path_prefix=f"{path}.fields", issues=issues)
        if field.item is not None:
            _lint_item(field.item, path=f"{path}.item", issues=issues)


def _lint_item(item: FieldSpec, *, path: str, issues: list[LintIssue]) -> None:
    # Array elements are positional. Skip name-/description-of-self checks when
    # unnamed; still recurse into nested structure.
    if item.name is not None:
        if _is_reserved(item.name):
            issues.append(
                LintIssue(
                    path=path,
                    code="reserved-name",
                    message=f"item name {item.name!r} is reserved",
                )
            )
        if not _has_text(item.description):
            issues.append(
                LintIssue(
                    path=path,
                    code="missing-description",
                    message=f"item {item.name!r} has no description",
                )
            )

    if item.fields is not None:
        _lint_fields(item.fields, path_prefix=f"{path}.fields", issues=issues)
    if item.item is not None:
        _lint_item(item.item, path=f"{path}.item", issues=issues)


def _lint_labels(labels: list[Label], *, issues: list[LintIssue]) -> None:
    seen: set[str] = set()
    for idx, label in enumerate(labels):
        path = f"labels[{idx}]"
        name = label.name

        if name in seen:
            issues.append(
                LintIssue(
                    path=path,
                    code="duplicate-label-name",
                    message=f"duplicate label name {name!r}",
                )
            )
        else:
            seen.add(name)

        if _is_reserved(name):
            issues.append(
                LintIssue(
                    path=path,
                    code="reserved-name",
                    message=f"label name {name!r} is reserved",
                )
            )

        if not _has_text(label.description):
            issues.append(
                LintIssue(
                    path=path,
                    code="missing-description",
                    message=f"label {name!r} has no description",
                )
            )


def _is_reserved(name: str) -> bool:
    return keyword.iskeyword(name) or name in _PYDANTIC_RESERVED


def _has_text(s: str | None) -> bool:
    return s is not None and s.strip() != ""
