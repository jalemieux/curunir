# Coordination note → the C (finance-eval) thread

**From:** the A/B/D (balance-sheet capability) design thread
**Date:** 2026-06-04
**Why:** A/B/D resolved two decisions that change C's fixture/anchor design,
and C is going to be the *judge* of an interface experiment. Sync before C's
fixture is finalized so the threads don't diverge.

## What changed vs. the current C spec

The C design spec assumes a **`holdings.json`** fixture + a **`_networth.py`**
anchor. A/B/D has since locked a different store:

1. **Store = SQLite**, not JSON. The canonical portfolio lives in
   `context/memory/portfolio.db` (tables `assets`, `liabilities`; views
   `v_networth`, `v_rollup_by_class`, `v_collectibles_pnl`). A single Python
   **engine** module is the source of truth for all reads/writes.

2. **The portfolio is reached through a Python engine, exposed two ways** —
   a **CLI** adapter and an **opt-in tool** adapter — and **C is meant to pick
   the winner** by running the same suite in each mode on the local model.

## Concrete asks of the C thread

- **Fixture format:** make the seeded portfolio a **`fixtures/portfolio.sql`**
  (or a checked-in seed `.db` built from it), not `holdings.json`. Same
  synthetic-but-faithful asset set, frozen values.
- **Anchor:** replace `_networth.py`'s bespoke math with calls into the **same
  engine / views** the agent uses (e.g. `SELECT * FROM v_networth`,
  `v_rollup_by_class`, `v_collectibles_pnl`). This keeps the anchoring
  philosophy intact — agent and grader compute identically — and means the
  anchor is just thin wrappers over the engine.
- **Two-mode runs:** the suite should be runnable against the SUT in
  **CLI-interface mode** and **tool-interface mode**, reported separately, so
  the pass-rate + tool-call-efficiency delta picks the surface. Grading stays
  at the boundary (`final_text` / correct net worth), so the tasks themselves
  shouldn't need to change between modes — only the SUT config.
- **Fixture seed/restore** still applies (stash real `context/memory/`, drop in
  the fixture DB, run, restore; local SUT only).

## What's unchanged / still compatible

- The **three task families** (P hallucination, T reconciliation, W
  write/readback) and the **boundary-only** grading philosophy are unaffected.
- The new **`reconciles`** grader is still valid — it parses the agent's stated
  totals; it doesn't care whether the store is JSON or SQLite.
- **Frozen fixture values** decision stands (a portfolio benchmark must be
  reproducible; don't anchor to live prices).

## The dependency, stated plainly

A/B/D ships the **engine + views**; C's anchor should *consume* them rather
than reimplement the math. So the cleanest ordering is: A/B/D lands the engine
+ schema, C points its fixture/anchor at it, then C runs both interface modes.
If C needs to keep moving before the engine exists, build the fixture `.sql`
and the task list now (interface-agnostic), and wire the anchor to the engine
once it lands.
