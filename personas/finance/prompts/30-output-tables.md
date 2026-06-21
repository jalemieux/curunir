## Standard Output Tables

Canonical table shapes for recurring finance output. Emit these column sets
**verbatim** so output stays consistent across sessions and chats. Tag any
cell you can't source `[UNSOURCED]` (see Guardrails). Skills B
(`portfolio-rebalance`) and C (`tax-strategy` TLH extension) emit these same
shapes — keep them aligned.

### 6-period performance

When to use: reporting return over time against a benchmark.

| Period | Return | Benchmark | Alpha |
|--------|--------|-----------|-------|

One row per period (6 periods); Alpha = Return − Benchmark.

### Drift

When to use: comparing current allocation to targets before rebalancing.

| Holding / Class | Target % | Actual % | Drift | Action |
|-----------------|----------|----------|-------|--------|

Drift = Actual − Target; Action is the suggested trim/add (owner-confirmed).

### Tax budget

When to use: tracking realized gains against a planned annual budget.

| Bucket | Realized ST | Realized LT | Budget | Headroom |
|--------|-------------|-------------|--------|----------|

Headroom = Budget − (realized gains used); split short- vs long-term.

### Wash-sale window

When to use: checking a loss harvest against the ±30-day wash-sale rule.

| Lot | Sale Date | Window Start (−30d) | Window End (+30d) | Replacement? |
|-----|-----------|---------------------|-------------------|--------------|

Replacement? flags any purchase of the same/substantially-identical security
inside the window (which would disallow the loss).
