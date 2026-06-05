#!/usr/bin/env python3
"""Seed context/memory/portfolio.db for the balance-sheet capability.

This is a SCAFFOLD. The rows below are SYNTHETIC EXAMPLES — replace them with
the owner's real figures at migration time, and do NOT commit real holdings to
git. The real migration is an owner-present step: drop in the actual rows (or
import brokerage CSVs via the balance-sheet `import_rows` path), resolve the
ambiguity checklist printed at the end, then verify with `portfolio.py networth`.

Refuses to run if the DB already has assets, to avoid duplicate seeding."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.portfolio import db as pdb       # noqa: E402
from src.portfolio import engine          # noqa: E402

DB = "context/memory/portfolio.db"

# EXAMPLE rows — SYNTHETIC. Replace with the owner's real data at migration
# time. Do NOT commit real holdings.
ASSETS = [
    {"id": "primary-home", "class": "real_estate", "label": "Primary residence",
     "value": 1000000, "cost_basis": 600000, "acquired": "2018"},
    {"class": "physical", "label": "Physical gold (example)", "value": 20000},
    {"class": "collectible", "label": "Example watch", "value": 10000},
]
LIABILITIES = [
    {"id": "home-mtg", "class": "mortgage", "label": "Home mortgage",
     "balance": 400000, "apr": 3.0, "linked_asset": "primary-home"},
]

AMBIGUITIES = [
    "GOLD: confirm whether a gold ETF (e.g. GLD) and physical bullion are BOTH "
    "held (two separate assets) or the same — avoid double-counting. Import the "
    "ETF with the brokerage CSV; record physical separately as class=physical.",
    "COLLECTIBLES: set cost_basis + acquired for every piece so holding period "
    "and the 28% collectibles rate can be computed.",
    "BROKERAGE EQUITIES: import each account from its CSV via the balance-sheet "
    "`import_rows` path, passing the account's stated total as stated_total.",
]


def main() -> int:
    pdb.init_db(DB)
    if engine.list_assets(DB):
        print("refusing to migrate: portfolio.db already has assets")
        return 1
    for a in ASSETS:
        engine.add_asset(DB, a)
    for liab in LIABILITIES:
        engine.add_liability(DB, liab)
    print("seeded (EXAMPLE data):", engine.networth(DB))
    print("\nRESOLVE WITH OWNER (replace example data with real figures):")
    for i, note in enumerate(AMBIGUITIES, 1):
        print(f"  {i}. {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
