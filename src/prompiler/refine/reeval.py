"""Re-eval delta surfacing (P5, L212).

After a tutor patch is applied the eval is re-run; this module turns the two
:class:`~prompiler.eval.metrics.Metrics` snapshots into a signed delta so the
caller can show whether the edit moved precision / recall / F1 and by how much
(architecture.md §2.6: "surface metric delta vs previous report").

The math is pure and dependency-free — the actual re-run (invoking ``run_eval``
twice) is orchestration that belongs in the CLI / E2E layer, exactly as the
differ keeps ``apply_patch`` free of any backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from prompiler.eval.metrics import Metrics

_AGGREGATE_KEYS = ("precision", "recall", "f1")


@dataclass(frozen=True)
class MetricDelta:
    """A single scalar's before / after value and its signed movement."""

    name: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class FieldDelta:
    """Per-field F1 before / after value and its signed movement."""

    field: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class ReevalDelta:
    """Aggregate and per-field movement between two eval snapshots."""

    aggregate: Mapping[str, MetricDelta]
    per_field: Mapping[str, FieldDelta]

    @property
    def improved(self) -> bool:
        return self.aggregate["f1"].delta > 0.0

    @property
    def regressed(self) -> bool:
        return self.aggregate["f1"].delta < 0.0


def compute_delta(before: Metrics, after: Metrics) -> ReevalDelta:
    """Diff two ``Metrics`` snapshots into aggregate and per-field deltas.

    Fields present in only one snapshot are treated as 0.0 on the missing side,
    so an added field shows full uplift and a dropped field shows full regression.
    """
    aggregate = {
        key: MetricDelta(name=key, before=getattr(before, key), after=getattr(after, key))
        for key in _AGGREGATE_KEYS
    }
    fields = sorted(set(before.per_field) | set(after.per_field))
    per_field = {
        field: FieldDelta(
            field=field,
            before=before.per_field[field].f1 if field in before.per_field else 0.0,
            after=after.per_field[field].f1 if field in after.per_field else 0.0,
        )
        for field in fields
    }
    return ReevalDelta(aggregate=aggregate, per_field=per_field)


def render_delta(delta: ReevalDelta) -> str:
    """Render a human-readable aggregate summary with signed deltas.

    Each line reads ``<metric>: <before> -> <after> (<+/-delta>)`` at 3-decimal
    precision so a regression is visible by its leading minus sign.
    """
    lines = []
    for key in _AGGREGATE_KEYS:
        md = delta.aggregate[key]
        lines.append(f"{key}: {md.before:.3f} -> {md.after:.3f} ({md.delta:+.3f})")
    return "\n".join(lines)
