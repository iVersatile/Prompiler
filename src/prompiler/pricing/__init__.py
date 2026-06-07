"""Shipped pricing table and its degrade-never loader."""

from __future__ import annotations

from prompiler.pricing.loader import (
    PricingLoad,
    default_pricing_path,
    load_pricing,
)

__all__ = ["PricingLoad", "default_pricing_path", "load_pricing"]
