# Peer Comparables

A peer comp table grounds the analysis. Two questions to answer with it:

1. **Where does the target sit relative to peers** on key multiples and
   growth/margin metrics?
2. **Is the peer set itself fairly priced** vs. its history? (Sector-wide
   re-ratings change what "in-line with peers" means.)

## Building the peer set

The yfinance auto-peer list is a starting point, not the answer. Curate
**3-5 peers** by hand using these criteria:

- **Same industry, similar business model.** Lilly's peers are large-cap
  innovative pharma (PFE, MRK, NVO, BMY, JNJ) — not generics, not
  biotech-only, not med-tech.
- **Similar size.** Peers within ~0.3x to ~3x the target's market cap.
  Comparing a $700B company to a $10B one is noise.
- **Similar geographic footprint.** Pure-US vs. global multinationals
  trade differently.
- **Similar leverage profile** if you're using EV/EBITDA — high-leverage
  peers compress EV/EBITDA artificially.

Briefly justify each peer in the assumptions block ("PFE: similar scale,
overlapping therapy areas; NVO: comparable GLP-1 exposure"). When in
doubt, **fewer high-quality peers > more so-so peers**.

## What to include in the table

Always:

| Column | Source | Why |
|---|---|---|
| Ticker | n/a | identification |
| Market cap (\$B) | yfinance.profile | size context |
| Revenue TTM (\$B) | yfinance.financials | scale |
| Revenue growth YoY | yfinance.financials | growth comparison |
| Operating margin | yfinance.financials | quality comparison |
| Trailing P/E | yfinance.multiples | valuation |
| EV/EBITDA | yfinance.multiples | leverage-neutral valuation |
| Forward P/E | yfinance.multiples | forward-looking valuation |

Keep numbers consistent in units (\$B for cap and revenue, % for growth
and margins, x for multiples, 1 decimal place).

Bold or callout the **target** row so it's visually obvious where it sits.

## Reading the table — what to write below it

Don't just paste the table. Write 2-3 sentences pointing the reader at
what matters:

- **Where target sits on each key multiple** — premium / in-line /
  discount vs. peer median.
- **What that gap is justified by** — premium growth? higher margins?
  unique pipeline? Or unjustified?
- **Any peer that's a notable outlier** and what's driving it (recent
  M&A, one-time event, structurally different business).

## Example commentary

> Lilly trades at a meaningful premium to peer-set median P/E (62x vs.
> ~16x), reflecting the market's expectations for GLP-1 franchise growth.
> The premium is most defensible relative to MRK and BMY (mature, slower
> growth) and least defensible relative to NVO, the closest analog on
> GLP-1 exposure, which trades at ~32x. This suggests the implied bar for
> Lilly's growth is roughly 2x what the market is asking of NVO.

That's the kind of paragraph that makes the table earn its place.

## Pitfalls

- **Mixing trailing and forward across rows.** Use trailing for one row,
  forward for another, never both in the same column.
- **Peers with negative earnings → undefined P/E.** Show "n/m" (not
  meaningful) and use EV/Revenue or forward P/E for those rows.
- **Stale numbers.** If two peers report on different fiscal calendars,
  one might have a quarter more of fresh data. Note this in the
  assumptions block — it can matter for growth comparisons.
- **Forgetting ADRs trade in USD but report in home currency.** NVO ADR
  on NYSE quotes USD; Novo's revenue is in DKK. yfinance harmonizes most
  things but spot-check for sanity.
