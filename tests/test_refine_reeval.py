"""TDD suite for the refine re-eval delta surfacing (P5, L212).

The pure core compares two :class:`~prompiler.eval.metrics.Metrics` snapshots
(before vs after a tutor patch) and surfaces the precision / recall / F1 delta,
plus the per-field F1 movement, with regression / improvement flags. The actual
"re-run eval" is orchestration left to the CLI; only the delta math is unit
tested here — mirroring how the differ keeps ``apply_patch`` pure.
"""

from __future__ import annotations

import pytest

from prompiler.eval.metrics import FieldScore, Metrics
from prompiler.refine.reeval import (
    MetricDelta,
    ReevalDelta,
    compute_delta,
    render_delta,
)

pytestmark = pytest.mark.unit


def _metrics(
    *,
    precision: float,
    recall: float,
    f1: float,
    per_field: dict[str, FieldScore] | None = None,
) -> Metrics:
    return Metrics(
        per_field=per_field or {},
        tp=0,
        fp=0,
        fn=0,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _field(name: str, f1: float) -> FieldScore:
    return FieldScore(field=name, tp=0, fp=0, fn=0, precision=f1, recall=f1, f1=f1)


def test_metric_delta_holds_before_after_and_signed_delta() -> None:
    md = MetricDelta(name="f1", before=0.5, after=0.8)
    assert md.delta == pytest.approx(0.3)


def test_compute_delta_surfaces_aggregate_movement() -> None:
    before = _metrics(precision=0.5, recall=0.4, f1=0.44)
    after = _metrics(precision=0.7, recall=0.6, f1=0.65)

    delta = compute_delta(before, after)

    assert isinstance(delta, ReevalDelta)
    assert delta.aggregate["precision"].before == pytest.approx(0.5)
    assert delta.aggregate["precision"].after == pytest.approx(0.7)
    assert delta.aggregate["precision"].delta == pytest.approx(0.2)
    assert delta.aggregate["recall"].delta == pytest.approx(0.2)
    assert delta.aggregate["f1"].delta == pytest.approx(0.21)


def test_compute_delta_flags_improvement_when_f1_rises() -> None:
    before = _metrics(precision=0.5, recall=0.5, f1=0.5)
    after = _metrics(precision=0.6, recall=0.6, f1=0.6)

    delta = compute_delta(before, after)

    assert delta.improved is True
    assert delta.regressed is False


def test_compute_delta_flags_regression_when_f1_drops() -> None:
    before = _metrics(precision=0.6, recall=0.6, f1=0.6)
    after = _metrics(precision=0.5, recall=0.5, f1=0.5)

    delta = compute_delta(before, after)

    assert delta.regressed is True
    assert delta.improved is False


def test_compute_delta_unchanged_f1_is_neither_improved_nor_regressed() -> None:
    before = _metrics(precision=0.6, recall=0.6, f1=0.6)
    after = _metrics(precision=0.6, recall=0.6, f1=0.6)

    delta = compute_delta(before, after)

    assert delta.improved is False
    assert delta.regressed is False
    assert delta.aggregate["f1"].delta == pytest.approx(0.0)


def test_compute_delta_surfaces_per_field_f1_movement() -> None:
    before = _metrics(
        precision=0.5,
        recall=0.5,
        f1=0.5,
        per_field={"name": _field("name", 0.4), "date": _field("date", 0.9)},
    )
    after = _metrics(
        precision=0.6,
        recall=0.6,
        f1=0.6,
        per_field={"name": _field("name", 0.7), "date": _field("date", 0.9)},
    )

    delta = compute_delta(before, after)

    assert delta.per_field["name"].before == pytest.approx(0.4)
    assert delta.per_field["name"].after == pytest.approx(0.7)
    assert delta.per_field["name"].delta == pytest.approx(0.3)
    assert delta.per_field["date"].delta == pytest.approx(0.0)


def test_compute_delta_handles_field_added_after_patch() -> None:
    before = _metrics(precision=0.5, recall=0.5, f1=0.5, per_field={"name": _field("name", 0.4)})
    after = _metrics(
        precision=0.6,
        recall=0.6,
        f1=0.6,
        per_field={"name": _field("name", 0.6), "extra": _field("extra", 0.8)},
    )

    delta = compute_delta(before, after)

    assert delta.per_field["extra"].before == pytest.approx(0.0)
    assert delta.per_field["extra"].after == pytest.approx(0.8)
    assert delta.per_field["extra"].delta == pytest.approx(0.8)


def test_compute_delta_handles_field_dropped_after_patch() -> None:
    before = _metrics(
        precision=0.5,
        recall=0.5,
        f1=0.5,
        per_field={"name": _field("name", 0.4), "gone": _field("gone", 0.7)},
    )
    after = _metrics(precision=0.6, recall=0.6, f1=0.6, per_field={"name": _field("name", 0.6)})

    delta = compute_delta(before, after)

    assert delta.per_field["gone"].before == pytest.approx(0.7)
    assert delta.per_field["gone"].after == pytest.approx(0.0)
    assert delta.per_field["gone"].delta == pytest.approx(-0.7)


def test_render_delta_reports_aggregate_and_arrow_direction() -> None:
    before = _metrics(precision=0.5, recall=0.5, f1=0.5)
    after = _metrics(precision=0.7, recall=0.6, f1=0.65)

    text = render_delta(compute_delta(before, after))

    assert "f1" in text
    assert "0.500" in text
    assert "0.650" in text
    assert "+0.150" in text


def test_render_delta_marks_regression_with_signed_negative() -> None:
    before = _metrics(precision=0.6, recall=0.6, f1=0.6)
    after = _metrics(precision=0.5, recall=0.5, f1=0.5)

    text = render_delta(compute_delta(before, after))

    assert "-0.100" in text
