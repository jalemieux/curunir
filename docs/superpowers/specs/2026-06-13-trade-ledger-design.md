# Trade Ledger — Design (active, specific-lot)

GitHub issue: [#377](https://github.com/jalemieux/curunir/issues/377)

## Problem

The `balance-sheet` portfolio DB (`context/memory/portfolio.db`) tracks **current
holdings** only. Each position carries cost basis, acquired date, current value,
and account — but there is no **transaction ledger**: no buy/sell history, no
realized P/L on closed positions, no lot-level draw-down audit. Selling part of a
position requires a manual `set`/`rm`, which silently drops the realized gain and
all execution detail.

## Decisions (from brainstorming)

- **Active ledger** — recording a trade adjusts the linked position automatically;
  the trade is the single entry point for a position change.
- **Specific-lot sells** — the user names the lot (`asset_id`) to draw down. No
  cross-lot auto-split; one trade draws from one lot.
- **Buys create a new lot row** (own cost basis + acquired date), matching the
  existing one-row-per-lot convention (e.g. VOO already has ~20 rows).
- **Qty-bearing classes only** — equity, physical. Real estate, collectibles,
  cash, private are out of scope (no share-level fills).

## Architecture

A new **`trades`** node inside the existing `portfolio.db` (it references asset
lot ids, so it shares the store). The `assets` table stays "current holdings";
the ledger is both the **event log** and the **entry point** for position
changes on traded assets.

```
              portfolio tool / CLI / balance-sheet skill
                              │
         ┌────────────┬───────┴───────┬─────────────┐
       buy          sell           trades        realized
         │            │               │              │
         ▼            ▼               ▼              ▼
   ┌──────────────────────────────────────────────────────┐
   │                      engine.py                          │
   │  record_buy  ── creates NEW lot ──► assets             │
   │              └─ appends ──────────► trades  (NEW)      │
   │  record_sell ── draws down lot ───► assets             │
   │              └─ appends w/ P&L ───► trades             │
   │  trade_history / realized_pnl ──── read ─────► trades  │
   └──────────────────────────────────────────────────────┘
```

## Data model — `trades` table

| column           | type | notes                                              |
|------------------|------|----------------------------------------------------|
| `id`             | TEXT PK | slug, e.g. `t-2026-07-03-spcx-sell` (deduped)   |
| `trade_date`     | TEXT | execution date (ISO)                               |
| `side`           | TEXT | `buy` \| `sell`                                    |
| `ticker`         | TEXT | symbol traded                                      |
| `qty`            | REAL | shares                                             |
| `price`          | REAL | execution price per share                          |
| `fees`           | REAL | default 0                                          |
| `asset_id`       | TEXT | the lot (created on buy, drawn down on sell)       |
| `account`        | TEXT | optional                                           |
| `cost_basis_sold`| REAL | sell only — basis of the shares sold               |
| `proceeds`       | REAL | sell only — `qty·price − fees`                     |
| `realized_pnl`   | REAL | sell only — `proceeds − cost_basis_sold`           |
| `long_term`      | INTEGER | sell only — 1 if `acquired→trade_date ≥ 1yr`    |
| `note`           | TEXT | optional free text                                 |
| `created_at`     | TEXT | wall-clock insert time                             |

`asset_id` is a soft reference (no FK): a sell that closes a lot deletes the
asset row, but the trade row must survive as history.

## Engine functions

**`record_buy(path, fields)`** — `fields`: `ticker`, `qty`, `price`, `trade_date`,
optional `class` (default `equity`), `fees`, `account`, `label`, `note`.
1. Validate class ∈ {equity, physical}; require ticker/qty/price/trade_date.
2. Create a **new lot** via `add_asset`: `cost_basis = qty·price + fees`,
   `avg_cost = price`, `value = qty·price`, `value_asof = acquired = trade_date`,
   `label` defaults to `<TICKER> <trade_date>`.
3. Append a `buy` trade referencing that lot.
4. Return `{trade_id, asset_id, lot}`.

**`record_sell(path, fields)`** — `fields`: `asset_id` (the lot), `qty`, `price`,
`trade_date`, optional `fees`, `note`.
1. Load the lot; error if missing (`KeyError`), not qty-bearing, `cost_basis`
   None, or `qty > lot.qty` (`ValueError`).
2. `per_share_basis = lot.cost_basis / lot.qty`;
   `cost_basis_sold = qty · per_share_basis`;
   `proceeds = qty·price − fees`; `realized = proceeds − cost_basis_sold`.
3. `long_term = (trade_date − lot.acquired) ≥ 365.25 days` (when `acquired` set).
4. Decrement the lot: `qty -= sold`, `cost_basis -= cost_basis_sold`,
   `value = remaining_qty · price`, `value_asof = trade_date`. If remaining
   qty == 0 → `remove_asset` (lot closed).
5. Append a `sell` trade with the computed fields.
6. Return `{trade_id, realized_pnl, long_term, remaining_qty, lot_closed}`.

**`trade_history(path, ticker=None, account=None, side=None, since=None)`** —
filtered, newest-first (`trade_date` desc, then `created_at`).

**`realized_pnl(path, ticker=None, account=None, year=None)`** — sum realized
over sell trades, split `short_term` / `long_term` / `total`, filterable.

## Surfaces

- **`portfolio` tool** (`portfolio_tool.py`): add `buy`, `sell` (writes) and
  `trades`, `realized` (reads).
- **CLI** (`portfolio.py`): `buy`, `sell`, `trades`, `realized` subcommands.
- **SKILL.md**: a "Trades" section + discipline — *a sale goes through `sell`,
  never a manual `set`; the engine moves the position for you.*

## Error handling

| condition                         | result      |
|-----------------------------------|-------------|
| sell lot id not found             | `KeyError`  |
| sell qty > lot qty                | `ValueError`|
| lot missing `cost_basis`          | `ValueError`|
| unknown / ineligible class on buy | `ValueError`|
| missing ticker/qty/price/date     | `ValueError`|

## Out of scope (future)

Wash-sale detection, cross-lot FIFO auto-split, average-cost basis, trades
against non-qty assets (real estate, single collectibles), broker CSV trade
import.

## Testing

TDD against the engine first (the eval anchor — graders read the same store),
then tool and CLI shims. New cases extend `tests/test_portfolio_engine.py`,
`test_portfolio_tool.py`, `test_portfolio_cli.py`.
