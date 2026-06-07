"""Unit tests for the shipped pricing-table loader (P7 task 3)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from prompiler.backends.observability import DEFAULT_PRICING_TABLE
from prompiler.pricing import PricingLoad, default_pricing_path, load_pricing

pytestmark = pytest.mark.unit


def _write_pricing(path, *, valid_until: str, rates: list[dict[str, object]]) -> None:
    payload = {"version": 1, "valid_until": valid_until, "rates": rates}
    path.write_text(json.dumps(payload), encoding="utf-8")


_DEFAULT_RATES: list[dict[str, object]] = [
    {
        "backend": "claude",
        "model": "claude-haiku-4-5-20251001",
        "prompt_per_1m": 1.0,
        "completion_per_1m": 5.0,
    },
    {
        "backend": "openai",
        "model": "gpt-4o-mini",
        "prompt_per_1m": 0.15,
        "completion_per_1m": 0.60,
    },
    {
        "backend": "gemini",
        "model": "gemini-2.5-flash",
        "prompt_per_1m": 0.30,
        "completion_per_1m": 2.50,
    },
    {
        "backend": "ollama",
        "model": "llama3.1",
        "prompt_per_1m": 0.0,
        "completion_per_1m": 0.0,
    },
]


def test_default_pricing_path_points_at_shipped_json() -> None:
    path = default_pricing_path()
    assert path.name == "v1.json"
    assert path.exists()


def test_shipped_pricing_matches_default_table() -> None:
    load = load_pricing(today=date(2020, 1, 1))
    assert isinstance(load, PricingLoad)
    assert load.warnings == ()
    # Shipped JSON must mirror the hardcoded fallback exactly.
    assert load.table.rates == DEFAULT_PRICING_TABLE.rates


def test_valid_file_loads_into_pricing_table(tmp_path) -> None:
    pfile = tmp_path / "v1.json"
    _write_pricing(pfile, valid_until="2999-12-31", rates=_DEFAULT_RATES)

    load = load_pricing(pfile, today=date(2026, 6, 7))

    assert load.warnings == ()
    assert load.valid_until == date(2999, 12, 31)
    assert load.table.cost_usd(
        backend="claude",
        model="claude-haiku-4-5-20251001",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    ) == pytest.approx(6.0)


def test_missing_file_falls_back_with_warning(tmp_path) -> None:
    load = load_pricing(tmp_path / "absent.json", today=date(2026, 6, 7))

    assert load.table.rates == DEFAULT_PRICING_TABLE.rates
    assert len(load.warnings) == 1
    assert "not found" in load.warnings[0].lower()


def test_malformed_json_falls_back_with_warning(tmp_path) -> None:
    pfile = tmp_path / "v1.json"
    pfile.write_text("{not valid json", encoding="utf-8")

    load = load_pricing(pfile, today=date(2026, 6, 7))

    assert load.table.rates == DEFAULT_PRICING_TABLE.rates
    assert len(load.warnings) == 1
    assert "malformed" in load.warnings[0].lower() or "parse" in load.warnings[0].lower()


def test_expired_valid_until_warns_but_still_loads(tmp_path) -> None:
    pfile = tmp_path / "v1.json"
    _write_pricing(pfile, valid_until="2020-01-01", rates=_DEFAULT_RATES)

    load = load_pricing(pfile, today=date(2026, 6, 7))

    # Expired table is still usable — never a hard failure.
    assert load.table.rates != {}
    assert load.valid_until == date(2020, 1, 1)
    assert len(load.warnings) == 1
    assert "expired" in load.warnings[0].lower() or "stale" in load.warnings[0].lower()


def test_expired_at_boundary_is_not_expired(tmp_path) -> None:
    pfile = tmp_path / "v1.json"
    _write_pricing(pfile, valid_until="2026-06-07", rates=_DEFAULT_RATES)

    load = load_pricing(pfile, today=date(2026, 6, 7))

    assert load.warnings == ()


def test_malformed_valid_until_warns_and_falls_back(tmp_path) -> None:
    pfile = tmp_path / "v1.json"
    pfile.write_text(
        json.dumps({"version": 1, "valid_until": "not-a-date", "rates": _DEFAULT_RATES}),
        encoding="utf-8",
    )

    load = load_pricing(pfile, today=date(2026, 6, 7))

    assert load.table.rates == DEFAULT_PRICING_TABLE.rates
    assert len(load.warnings) == 1
