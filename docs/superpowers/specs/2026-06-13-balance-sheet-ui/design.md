# Balance Sheet UI redesign (Local Console) — design

**Date:** 2026-06-13
**Status:** approved
**Scope:** read-only redesign of the Local Web UI "Balance Sheet" tab, plus a
trades / realized-P&L view. All writes stay in chat / CLI.

## Problem

The current Balance Sheet tab (`src/local_ui/static/index.html`,
`loadPortfolio()`) is flat and hard to scan:

- Rollup is a 7-row table with no visual weight or allocation %.
- Holdings are one undifferentiated table (cash next to equities next to real
  estate), no grouping, no subtotals, no unrealized gain, qty/ticker unused.
- The trade ledger shipped in #379 (`trade_history`, `realized_pnl`) has **no
  UI** at all.

## Decision

**Option A — single-scroll dashboard.** One scrollable page, top-to-bottom:
net-worth hero → allocation bar → grouped holdings → trades + realized P&L.
Chosen over sub-tabs (B) and master-detail explorer (C) for least clicking and
best scannability. Mockup: `mockups.html` in this directory.

## Layout

```
┌─ Balance Sheet ───────────────────────────────────────────┐
│  $2,484,300            ← net-worth hero                     │
│  Values as of 2026-05-30   ← staleness caveat (oldest asof) │
│  [Assets] [Liabilities] [Net worth] [Unrealized gain]       │
│                                                              │
│  ALLOCATION                                                  │
│  ▓▓▓▓▓▓▓▒▒▒░░  ← stacked bar (net-worth composition)        │
│  ● Equities 74% ● RE eq 16% ● Cash 5% …   Liabilities $247k │
│                                                              │
│  HOLDINGS (collapsible groups, per-class columns, subtotal) │
│  ▸ Equities (4)              +$577k unrealized   $1,842,000  │
│  ▸ Cash (3)                                        $128,000  │
│  ▸ Real estate (1)          val−mortgage           $402,500  │
│  ▸ Collectibles (3)         +$18.3k unrealized      $74,800  │
│                                                              │
│  TRADES — fiscal year to date                                │
│  [Realized ST] [Realized LT] [Realized total]               │
│  date · side · ticker · qty · price · amount · realized · LT│
└──────────────────────────────────────────────────────────┘
```

## Data layer (graph: reuse existing engine read nodes)

```
                 readers.portfolio_overview(config)
                              │  (one endpoint, extended)
   ┌──────────┬──────────┬───┴────┬───────────┬──────────────┐
   ▼          ▼          ▼        ▼           ▼              ▼
networth()  rollup()  list_   unrealized() trade_history  realized_pnl
            (alloc)   assets()   (NEW)      (since=FY)     (year=FY)
                      (groups)
```

`portfolio_overview()` is extended (no new tab endpoint) to also return:

| key            | source                                              | use |
|----------------|-----------------------------------------------------|-----|
| `unrealized`   | **new** `engine.unrealized(path)` → `{cost_basis, value, unrealized}` | hero card |
| `trades`       | `engine.trade_history(path, since="<YYYY>-01-01")`  | ledger table |
| `realized`     | `engine.realized_pnl(path, year=<YYYY>)`            | ST/LT/total cards |
| `as_of`        | min/max `value_asof` across holdings                | staleness caveat |

`<YYYY>` = current calendar year (= fiscal year; a non-calendar FY would need a
config knob — out of scope). Trades-list period and realized-P&L period are
the **same** window by construction.

### New engine node: `engine.unrealized(path)`

Portfolio-wide unrealized gain. Mirrors the shape of the existing `pnl()` so the
math stays in the engine (the UI never sums — no drift from the CLI):

```python
def unrealized(path: str) -> dict:
    """Portfolio-wide cost basis, market value, and unrealized gain over every
    asset that has a cost_basis. Assets without a cost_basis are excluded from
    BOTH sides (can't compute a gain), matching pnl()."""
    basis = value = 0.0
    for a in list_assets(path):
        cb = a.get("cost_basis")
        if cb in (None, ""):
            continue
        basis += float(cb)
        value += float(a["value"]) if a["value"] is not None else 0.0
    return {"cost_basis": round(basis, 2), "value": round(value, 2),
            "unrealized": round(value - basis, 2)}
```

### Allocation buckets

`engine.rollup()`'s positive buckets — equities, real_estate_equity,
collectibles, physical, cash, private — rendered as a stacked bar + legend with
%. `debt` is **not** a bar segment; it's a separate "Liabilities" line. Standard
net-worth-composition convention; reuses `rollup()` verbatim.

### Holdings groups

`engine.list_assets()` grouped by `class`, retirement folded under Equities to
match the rollup. Friendly labels, stable order
(equity → cash → real_estate → collectible → physical → private), each group
collapsible with a header subtotal and per-class columns:

| group        | columns |
|--------------|---------|
| Equities     | ticker · qty · avg cost · value · unrealized · account |
| Cash         | account · value |
| Real estate  | property · value · mortgage · equity *(via `re_equity`/linked liabilities — see note)* |
| Collectibles / Physical | item · cost · value · unrealized · held (LT/ST) |
| Private      | label · value · account |

Real-estate mortgage/equity columns reuse the linked-liability logic already in
`rollup()`/`re_equity()`; v1 may render value only if wiring per-property equity
is heavy, with the netted figure in the group subtitle.

## Frontend

One rewrite of `loadPortfolio()` in `index.html` + scoped CSS (groups,
allocation bar, badges). No framework — matches existing panels. Vanilla DOM,
same `apiGet("/api/portfolio")` call.

## Testing

- `engine.unrealized`: assets with/without cost_basis, empty store, negative
  gain. (`tests/test_portfolio_engine.py`)
- `readers.portfolio_overview`: new keys present and equal to the engine calls;
  `trades`/`realized` keyed to current year; `as_of` range; missing-DB still
  `available=False`. (`tests/test_local_ui_readers.py`)
- Frontend is manual (no JS test harness in repo).

## Out of scope

Daily net-worth delta / "top movers" (needs a snapshot history the engine
doesn't keep), live re-pricing from the UI, non-calendar fiscal year, any write
surface.
