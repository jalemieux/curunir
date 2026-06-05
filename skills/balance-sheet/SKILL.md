---
name: balance-sheet
description: "Use to track the owner's personal balance sheet — holdings across every asset class (equities, real estate, collectibles, physical/commodities, cash, private/PE), liabilities, cost basis, and net worth. Trigger phrases: 'what's my net worth', 'track my <asset>', 'add this to my portfolio', 'what's my equity in <property>', 'my watch collection value', 'import my brokerage CSV', 'how much <ticker> do I own', 'refresh my values', 'reconcile my accounts'. This is the owner's own book — distinct from financial-analysis (public companies) and investment-memo (theses)."
tools: portfolio
portal_summary: "Track your assets, liabilities, and net worth"
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
  `query` (read-only SELECT), `render`.
- **Writes:** `add`, `add_liability`, `set`, `rm`, `import_rows`, `refresh`.

Tool actions use the underscore names above. CLI subcommands match but
hyphenate multi-word names — `re-equity`, `add-liability`, `import-rows`
(the CLI `import-rows` takes `--rows-file <json>`).

## Disciplines (non-negotiable)

- **Never hand-compute a total.** Run `networth` / `rollup` and report what it
  returns. A net worth you summed yourself is a bug.
- **Never hand-edit the DB.** Use `add` / `set` / `rm`.
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
