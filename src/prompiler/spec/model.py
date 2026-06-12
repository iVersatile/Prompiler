"""EntitySpec / FieldSpec / Constraint / Label — Pydantic v2 model (PRD §5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FieldType = Literal[
    "string",
    "integer",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "enum",
    "array",
    "object",
]

_NUMERIC_TYPES: frozenset[str] = frozenset({"integer", "decimal"})

InputModality = Literal["text", "image", "audio"]

_DEFAULT_MODALITIES: list[InputModality] = ["text"]


class Constraint(BaseModel):
    """Cross-field constraint expression with severity."""

    model_config = ConfigDict(extra="forbid")

    expr: str
    severity: Literal["warn", "error"] = "warn"


class Label(BaseModel):
    """Classification label (one bucket in a classify task)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None


class FieldSpec(BaseModel):
    """Single field in an extract task. Recursive via `item` (array) / `fields` (object)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    type: FieldType
    required: bool = True
    description: str | None = None
    pattern: str | None = None
    values: list[str] | None = None
    item: FieldSpec | None = None
    fields: list[FieldSpec] | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _check_type_invariants(self) -> FieldSpec:
        t = self.type

        if t == "enum":
            if self.values is None:
                raise ValueError("enum field requires `values`")
            if len(self.values) == 0:
                raise ValueError("enum `values` must be non-empty")
            if len(set(self.values)) != len(self.values):
                raise ValueError("enum `values` must be unique")
        elif self.values is not None:
            raise ValueError("`values` only allowed on type=enum")

        if t == "array":
            if self.item is None:
                raise ValueError("array field requires `item`")
        elif self.item is not None:
            raise ValueError("`item` only allowed on type=array")

        if t == "object":
            if self.fields is None or len(self.fields) == 0:
                raise ValueError("object field requires non-empty `fields`")
        elif self.fields is not None:
            raise ValueError("`fields` only allowed on type=object")

        if self.pattern is not None and t != "string":
            raise ValueError("`pattern` only allowed on type=string")

        if (self.minimum is not None or self.maximum is not None) and t not in _NUMERIC_TYPES:
            raise ValueError("`minimum`/`maximum` only allowed on integer/decimal")

        return self


class EntitySpec(BaseModel):
    """Top-level spec: extract (fields) or classify (labels)."""

    model_config = ConfigDict(extra="forbid")

    spec_version: Literal[2]
    name: str
    task: Literal["extract", "classify"]
    description: str | None = None
    cite: bool = False
    fields: list[FieldSpec] | None = None
    labels: list[Label] | None = None
    cross_field_constraints: list[Constraint] = Field(default_factory=list)
    allow_multi_label: bool = False
    max_input_tokens: int | None = Field(default=None, ge=1)
    input_modalities: list[InputModality] = Field(default_factory=lambda: list(_DEFAULT_MODALITIES))

    @model_validator(mode="after")
    def _check_input_modalities(self) -> EntitySpec:
        if len(self.input_modalities) == 0:
            raise ValueError("`input_modalities` must be non-empty")
        if len(set(self.input_modalities)) != len(self.input_modalities):
            raise ValueError("`input_modalities` must be unique")
        return self

    @model_validator(mode="after")
    def _check_task_payload(self) -> EntitySpec:
        if self.task == "extract":
            if self.fields is None:
                raise ValueError("task=extract requires `fields`")
            if self.labels is not None:
                raise ValueError("task=extract must not have `labels`")
        elif self.task == "classify":
            if self.labels is None:
                raise ValueError("task=classify requires `labels`")
            if self.fields is not None:
                raise ValueError("task=classify must not have `fields`")
        return self


FieldSpec.model_rebuild()
