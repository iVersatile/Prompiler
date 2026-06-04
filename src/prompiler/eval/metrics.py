"""Precision / recall / F1 metrics for the eval harness (P4).

Pure, dependency-free arithmetic over ``(field, status)`` observations so the
metric math is unit-testable without a live model. Status semantics follow
standard slot-extraction scoring:

    match    -> true positive
    mismatch -> false positive AND false negative
    missing  -> false negative   (expected a value, model produced none)
    extra    -> false positive   (model produced a value we did not expect)

Zero-denominator precision/recall/F1 collapse to ``0.0`` so empty fields do not
poison the aggregate.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

DiffStatus = Literal["match", "mismatch", "missing", "extra"]

_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


def _tokenize(value: str) -> frozenset[str]:
    """Lowercase and split on non-alphanumeric runs into a token set."""
    return frozenset(t for t in _TOKEN_SPLIT.split(value.lower()) if t)


def jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity in [0.0, 1.0]; empty operands score 0.0.

    Order-insensitive and punctuation/whitespace tolerant. Used as the
    zero-dependency fuzzy fallback for near-miss eval matching (L189).
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union


@dataclass(frozen=True)
class FieldScore:
    """Per-field confusion counts and derived precision / recall / F1."""

    field: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class Metrics:
    """Aggregate metrics plus the per-field breakdown."""

    per_field: Mapping[str, FieldScore]
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Compute (precision, recall, f1); any zero denominator yields 0.0."""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _counts_to_score(field: str, tp: int, fp: int, fn: int) -> FieldScore:
    precision, recall, f1 = prf(tp, fp, fn)
    return FieldScore(field=field, tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1)


def aggregate(observations: Iterable[tuple[str, DiffStatus]]) -> Metrics:
    """Fold ``(field, status)`` observations into per-field and overall metrics."""
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    for field, status in observations:
        if status == "match":
            tp[field] += 1
        elif status == "mismatch":
            fp[field] += 1
            fn[field] += 1
        elif status == "missing":
            fn[field] += 1
        else:  # "extra"
            fp[field] += 1
    fields = sorted(set(tp) | set(fp) | set(fn))
    per_field = {f: _counts_to_score(f, tp[f], fp[f], fn[f]) for f in fields}
    total_tp = sum(tp.values())
    total_fp = sum(fp.values())
    total_fn = sum(fn.values())
    precision, recall, f1 = prf(total_tp, total_fp, total_fn)
    return Metrics(
        per_field=per_field,
        tp=total_tp,
        fp=total_fp,
        fn=total_fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )
