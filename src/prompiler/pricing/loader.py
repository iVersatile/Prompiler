"""Load the shipped pricing table, degrading to the hardcoded fallback.

A missing, malformed, or expired ``pricing/v1.json`` is never a hard failure:
:func:`load_pricing` always returns a usable :class:`PricingTable` and records
the problem in :attr:`PricingLoad.warnings` so callers (``prompiler stats``,
per-call logs) can surface it without aborting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from prompiler.backends.observability import (
    DEFAULT_PRICING_TABLE,
    PricingEntry,
    PricingTable,
)

_PRICING_FILE = "v1.json"


@dataclass(frozen=True)
class PricingLoad:
    """Result of loading a pricing table, with any non-fatal warnings."""

    table: PricingTable
    valid_until: date | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def default_pricing_path() -> Path:
    """Path to the pricing JSON shipped beside this module."""
    return Path(__file__).resolve().parent / _PRICING_FILE


def _fallback(warning: str) -> PricingLoad:
    return PricingLoad(table=DEFAULT_PRICING_TABLE, valid_until=None, warnings=(warning,))


def _parse_rates(rates: list[dict[str, object]]) -> dict[tuple[str, str], PricingEntry]:
    parsed: dict[tuple[str, str], PricingEntry] = {}
    for row in rates:
        key = (str(row["backend"]), str(row["model"]))
        parsed[key] = PricingEntry(
            prompt_per_1m=float(row["prompt_per_1m"]),  # type: ignore[arg-type]
            completion_per_1m=float(row["completion_per_1m"]),  # type: ignore[arg-type]
        )
    return parsed


def load_pricing(path: Path | None = None, *, today: date | None = None) -> PricingLoad:
    """Load pricing from ``path`` (default: shipped JSON).

    Falls back to :data:`DEFAULT_PRICING_TABLE` with a warning when the file is
    missing, unparseable, or has a malformed ``valid_until``. An expired (but
    otherwise valid) file still loads — only a staleness warning is added.
    """
    resolved = path if path is not None else default_pricing_path()
    today = today if today is not None else date.today()

    if not resolved.exists():
        return _fallback(f"pricing table not found at {resolved}; using built-in rates")

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _fallback(f"pricing table at {resolved} is malformed; using built-in rates")

    try:
        valid_until = date.fromisoformat(str(payload["valid_until"]))
        rates = _parse_rates(payload["rates"])
    except (KeyError, ValueError, TypeError):
        return _fallback(f"pricing table at {resolved} is malformed; using built-in rates")

    table = PricingTable(rates=rates)
    if valid_until < today:
        return PricingLoad(
            table=table,
            valid_until=valid_until,
            warnings=(
                f"pricing table at {resolved} expired on {valid_until.isoformat()}; "
                "rates may be stale",
            ),
        )
    return PricingLoad(table=table, valid_until=valid_until, warnings=())
