---
name: balance-sheet
description: "Use to track the owner's personal portfolio — holdings across every asset class (equities, real estate, collectibles, physical/commodities, cash, private/PE), liabilities, trades (buys/sells + realized P/L), cost basis, and net worth. Trigger phrases: 'what's my net worth', 'track my <asset>', 'add this to my portfolio', 'what's my equity in <property>', 'my watch collection value', 'import my brokerage CSV', 'how much <ticker> do I own', 'refresh my values', 'reconcile my accounts', 'I bought <ticker>', 'I sold <n> shares at <price>', 'log my trade', 'record a buy/sell', 'what's my realized gain', 'my trade history', 'realized P&L this year'. This is the owner's own book — distinct from financial-analysis (public companies) and investment-memo (theses)."
tools: portfolio
portal_summary: "Track your assets, liabilities, trades, and net worth"
---

# Balance Sheet

Track the owner's assets and liabilities in a structured store and answer
questions about them. **The engine does every calculation and every write** —
you never hand-sum a total or hand-edit the data.

## Data model

The store (`context/memory/portfolio.db`, SQLite) holds `assets` and
`liabilities`. Each asset has a `class` (equity, real_estate, collectible,
physical, cash, private, retirement), a `label`, a current `value`, and —
critically — `cost_basis` and `acquired` (acquisition date). Liabilities
(mortgage, loc, loan) carry a `balance` and may link to an asset (a mortgage →
its property).

## How you reach it

Reach the engine through the `portfolio` tool when it is available to you
(call it with an `action` and an `args` object). Otherwise run the CLI via
bash: `python skills/balance-sheet/portfolio.py <cmd>`. Both front the same
engine.

- **Reads:** `networth`, `rollup`, `list`, `show`, `re_equity`, `pnl`,
  `query` (read-only SELECT), `render`, `trades`, `realized`, `snapshots`,
  `show_snapshot`, `diff_snapshots`.
- **Writes:** `add`, `add_liability`, `set`, `rm`, `import_rows`, `refresh`,
  `buy`, `sell`, `snapshot`.

Tool actions use the underscore names above. CLI subcommands match but
hyphenate multi-word names — `re-equity`, `add-liability`, `import-rows`
(the CLI `import-rows` takes `--rows-file <json>`), `show-snapshot`,
`diff-snapshot`.

## Trades (the ledger)

`buy` / `sell` are the **active, specific-lot trade ledger** over qty-bearing
classes (equity, physical). They are the *entry point* for a position change:
the engine moves the position for you — you do **not** also `set`/`rm` the lot.

- **`buy`** (`ticker`, `qty`, `price`, `trade_date`; optional `class` (default
  equity), `fees`, `account`, `label`, `note`) mints a **new lot** with
  `cost_basis = qty·price + fees` and `acquired = trade_date`, then logs the
  trade. Each buy is its own lot — never merge into an existing row.
- **`sell`** (`asset_id` = the lot, `qty`, `price`, `trade_date`; optional
  `fees`, `note`) draws the **named lot** down, computes realized P/L
  (`(qty·price − fees) − qty·per-share-basis`), flags long/short-term, deletes
  the lot at zero qty, and logs the trade. The sale is specific-lot: when the
  owner sells, **ask which lot** (run `list --class equity` to show them) —
  one sell draws from one lot. Errors if the lot lacks the shares or a cost
  basis.
- **`trades`** (optional `ticker`/`account`/`side`/`since`) is the history,
  newest-first. **`realized`** (optional `ticker`/`account`/`year`) sums
  realized P/L, split short- vs long-term.

## Snapshots (point-in-time history)

The store keeps **current state only** — a `refresh` overwrites each value in
place, so prior marks are lost. To preserve history, freeze the full book into
the **append-only snapshot time-series**. A snapshot stores a *frozen copy* of
every asset + liability plus computed totals, so it survives later sales,
deletions, and re-pricing.

- **`snapshot`** (optional `trigger` (default `manual`), `note`, `force`) freezes
  the current book. **Dedup-aware:** a second snapshot with the same calendar
  date *and* trigger returns a `warning` (with the `existing` row) instead of
  inserting — pass `force=true` to add another the same day.
- **`snapshots`** (optional `since`/`until` dates) lists captures newest-first
  (id, date, trigger, net worth, holding counts).
- **`show_snapshot`** (`id`) returns one snapshot's full frozen state. `id`
  accepts an exact snapshot id, a date (`YYYY-MM-DD`), or `latest`.
- **`diff_snapshots`** (`a`, `b` — each an id, a date, or `latest`) reports the
  net-worth / asset / liability deltas (absolute + %) and a per-holding
  breakdown (**gained / lost / unchanged / new / closed**). Holdings are matched
  by `asset_id` first, falling back to `(class, label, ticker)` for a
  closed-and-reopened lot; genuinely ambiguous fallbacks are flagged in
  `ambiguous_matches` rather than guessed.
- **`refresh` with `snapshot_before=true`** freezes the pre-refresh state
  (trigger `refresh`) *before* re-pricing, so the change is recorded. Plain
  `refresh` writes no snapshot (unchanged default).
- Ad-hoc time-series come free from `query` over the snapshot tables — e.g.
  `SELECT taken_at, net_worth FROM v_snapshot_networth ORDER BY taken_at`.

**Scheduled snapshots (recipe).** To build a net-worth time-series, schedule a
periodic capture-and-report. Add a cron schedule (via the `schedule` tool / the
local UI) whose prompt loads this skill and: runs `refresh` with
`snapshot_before=true` (or a plain `snapshot`), then `diff_snapshots` of the two
most recent snapshots, and emails the diff. Weekly cadence keeps the series
useful without storage concern (no pruning is done). Example prompt: *"Use the
balance-sheet skill. Snapshot the portfolio, then diff the latest two snapshots
and email me the net-worth change and any new/closed positions."*

## Disciplines (non-negotiable)

- **Never hand-compute a total.** Run `networth` / `rollup` and report what it
  returns. A net worth you summed yourself is a bug.
- **Never hand-edit the DB.** Use `add` / `set` / `rm`.
- **Record trades through `buy` / `sell`, not `set`.** When the owner buys or
  sells a qty-bearing holding, log it as a trade — the engine moves the
  position *and* the realized P/L. A sell hand-applied with `set` silently
  drops the realized gain and the audit trail.
- **Capture cost basis + acquisition date on every asset.** `add` warns when
  they're missing — ask the owner for them rather than leaving them blank.
- **Pick the right `class`** so the asset lands in the right bucket (physical
  gold is `physical`, not jammed into an `equity` account).
- **Heed the dedup warning.** If `add` says a similar asset already exists,
  confirm with the owner before creating a second record.
- **Bulk-load brokerage exports with `import_rows`.** When the owner uploads a
  CSV (its content is already in your context), map the columns to the schema
  and pass the rows plus the export's stated account total as `stated_total` —
  the engine self-checks the sum and flags a dropped/miscopied row. Do not
  transcribe rows into one-by-one `add` calls.
- **Refresh on demand, not on every read.** Values reflect the last refresh.
  When the owner signals they want current/live/latest figures, run `refresh`
  first, then display. Otherwise show stored values.

## Privacy

These are the owner's real holdings. Never forward specific amounts to a third
party (see the persona guardrails).
