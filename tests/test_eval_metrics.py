"""Unit tests for prompiler.eval.metrics — precision/recall/F1 (P4).

Covers:
  - prf() zero-denominator collapse to 0.0 (no value, no prediction, no overlap)
  - prf() canonical TP/FP/FN arithmetic
  - aggregate() status folding: match/mismatch/missing/extra -> TP/FP/FN
  - aggregate() per-field breakdown + sorted field order
  - aggregate() empty observation stream
  - FieldScore / Metrics are frozen (immutable)
"""

from __future__ import annotations

import dataclasses

import pytest

from prompiler.eval.metrics import FieldScore, Metrics, aggregate, prf

# ---------------------------------------------------------------------------
# prf()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prf_perfect() -> None:
    precision, recall, f1 = prf(tp=4, fp=0, fn=0)
    assert (precision, recall, f1) == (1.0, 1.0, 1.0)


@pytest.mark.unit
def test_prf_mixed() -> None:
    precision, recall, f1 = prf(tp=2, fp=2, fn=2)
    assert precision == 0.5
    assert recall == 0.5
    assert f1 == 0.5


@pytest.mark.unit
def test_prf_all_zero_denominator() -> None:
    assert prf(tp=0, fp=0, fn=0) == (0.0, 0.0, 0.0)


@pytest.mark.unit
def test_prf_no_true_positives() -> None:
    # precision and recall both 0 -> f1 denominator 0 -> f1 0.0, no ZeroDivision
    precision, recall, f1 = prf(tp=0, fp=3, fn=3)
    assert (precision, recall, f1) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# aggregate()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_aggregate_empty() -> None:
    metrics = aggregate([])
    assert metrics.tp == 0
    assert metrics.fp == 0
    assert metrics.fn == 0
    assert metrics.f1 == 0.0
    assert dict(metrics.per_field) == {}


@pytest.mark.unit
def test_aggregate_status_folding() -> None:
    observations = [
        ("a", "match"),
        ("a", "mismatch"),
        ("b", "missing"),
        ("c", "extra"),
    ]
    metrics = aggregate(observations)
    # match -> tp; mismatch -> fp+fn; missing -> fn; extra -> fp
    assert metrics.tp == 1
    assert metrics.fp == 2  # mismatch on a + extra on c
    assert metrics.fn == 2  # mismatch on a + missing on b


@pytest.mark.unit
def test_aggregate_per_field_sorted() -> None:
    metrics = aggregate([("zeta", "match"), ("alpha", "match")])
    assert list(metrics.per_field.keys()) == ["alpha", "zeta"]


@pytest.mark.unit
def test_aggregate_per_field_counts() -> None:
    metrics = aggregate([("x", "match"), ("x", "match"), ("x", "missing")])
    score = metrics.per_field["x"]
    assert score.tp == 2
    assert score.fn == 1
    assert score.fp == 0
    assert score.recall == pytest.approx(2 / 3)
    assert score.precision == 1.0


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_field_score_frozen() -> None:
    score = FieldScore(field="x", tp=1, fp=0, fn=0, precision=1.0, recall=1.0, f1=1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        score.tp = 99  # type: ignore[misc]


@pytest.mark.unit
def test_metrics_frozen() -> None:
    metrics = aggregate([("x", "match")])
    assert isinstance(metrics, Metrics)
    with pytest.raises(dataclasses.FrozenInstanceError):
        metrics.tp = 99  # type: ignore[misc]
