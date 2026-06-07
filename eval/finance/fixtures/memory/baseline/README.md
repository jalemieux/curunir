# Memory System

This is a **synthetic eval fixture** seeded into `context/memory/` for finance
position-tracking runs (the T/W task families). It is not the owner's real
memory — the runner stashes the real `context/memory/` aside and restores it on
exit. See `eval/finance/fixtures/portfolio.sql` for the canonical source of
truth these notes describe.

## Where to look first

When the user asks about their portfolio, net worth, holdings, real estate,
watches, or gold — read **`portfolios.md`** before answering. It holds every
account, the real-estate equity, the watch collection, physical gold, and the
liabilities.

## Taxonomy

| File | Purpose |
|---|---|
| `portfolios.md` | The owner's complete balance sheet: brokerage / IRA / 401k / PE accounts, two properties + mortgages, watch collection (per-piece basis + dates), physical gold, cash, and the line of credit. |
