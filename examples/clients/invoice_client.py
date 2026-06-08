#!/usr/bin/env python
"""Scenario client: extract billing details from an invoice.

Runs hermetically by default (scripted backend, no API key). Set
``PROMPILER_LIVE=1`` and ``ANTHROPIC_API_KEY`` to hit a real model instead.

    python examples/clients/invoice_client.py
"""

from __future__ import annotations

from pathlib import Path

from prompiler import run_sync
from prompiler.runtime.registry import register_from_path

from _common import select_backend

SPEC_PATH = Path(__file__).resolve().parents[1] / "invoice.yaml"

DOCUMENT = """\
ACME OFFICE SUPPLIES LTD
Invoice #INV-2207
Bill to: Northwind Trading Co.

  Description                Qty     Amount
  A4 paper, 80gsm (box)       12     144.00
  Ballpoint pens (gross)       3      27.50
  Desk organiser, oak          5     210.00

Subtotal                              381.50
VAT (20%)                              76.30
Total due                             457.80 GBP
"""

# What a correct extraction looks like for the scripted (offline) run.
SCRIPTED_PAYLOAD = {"vendor_name": "ACME Office Supplies Ltd", "total_amount": "457.80"}


def main() -> None:
    register_from_path(str(SPEC_PATH))
    backend = select_backend(SCRIPTED_PAYLOAD)
    result = run_sync("invoice", DOCUMENT, backend=backend)
    print("vendor_name :", result.vendor_name)
    print("total_amount:", result.total_amount)


if __name__ == "__main__":
    main()
