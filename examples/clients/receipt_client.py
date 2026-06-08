#!/usr/bin/env python
"""Scenario client: extract purchase details from a point-of-sale receipt.

Runs hermetically by default (scripted backend, no API key). Set
``PROMPILER_LIVE=1`` and ``ANTHROPIC_API_KEY`` to hit a real model instead.

    python examples/clients/receipt_client.py
"""

from __future__ import annotations

from pathlib import Path

from prompiler import run_sync
from prompiler.runtime.registry import register_from_path

from _common import select_backend

SPEC_PATH = Path(__file__).resolve().parents[1] / "receipt.yaml"

DOCUMENT = """\
THE CORNER BISTRO
12 Market Street

2026-03-02  19:42

  2x Margherita pizza        24.00
  1x House salad              7.50
  3x Sparkling water          9.00

Subtotal                     40.50
Tax (8%)                      3.24
Tip                           6.00
TOTAL                        49.74
"""

# What a correct extraction looks like for the scripted (offline) run.
SCRIPTED_PAYLOAD = {
    "merchant": "The Corner Bistro",
    "purchased_at": "2026-03-02T19:42:00",
    "line_items": [
        {"description": "Margherita pizza", "quantity": 2, "amount": "24.00"},
        {"description": "House salad", "quantity": 1, "amount": "7.50"},
        {"description": "Sparkling water", "quantity": 3, "amount": "9.00"},
    ],
    "subtotal": "40.50",
    "tax": "3.24",
    "tip": "6.00",
    "total": "49.74",
}


def main() -> None:
    register_from_path(str(SPEC_PATH))
    backend = select_backend(SCRIPTED_PAYLOAD)
    result = run_sync("receipt", DOCUMENT, backend=backend)
    print("merchant   :", result.merchant)
    print("purchased_at:", result.purchased_at)
    print("line_items :", result.line_items)
    print("subtotal   :", result.subtotal)
    print("tax        :", result.tax)
    print("tip        :", result.tip)
    print("total      :", result.total)


if __name__ == "__main__":
    main()
