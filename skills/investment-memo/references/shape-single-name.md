# Shape: Single-Name Memo

Use when the memo argues a thesis on **one** instrument (equity, ETF,
commodity, crypto, or private name).

The shared memo header (Date, Prepared for, Thesis, Status, Executive
Summary, Investment Thesis long-form) is defined in `SKILL.md`. This file
covers what follows the header.

## Body outline

```markdown
## Setup

{2–3 paragraphs. What the business / instrument does, where it sits in
its market, the key revenue or value drivers. Skip company history unless
load-bearing for the thesis — readers don't need a Wikipedia paragraph.
For ETFs/commodities/crypto, this is "what the instrument tracks and what
drives its price." For private names, "what the company does and what
stage it's at."}

## Bull Case

{The argument *for* the thesis if the view is long; *against* it if
short. 3–6 numbered points, each with a brief justification and an inline
source cite for any load-bearing fact. Lead with the strongest point.}

## Bear Case

{The argument on the other side. Same shape — 3–6 numbered points, source
cites for load-bearing facts. Take it seriously; a weak bear section
signals motivated reasoning to the reader.}

## Financial Snapshot

{Compact block. For public equities:}
- Market cap: <value> (yfinance, <as-of date>)
- Revenue (TTM) / Earnings: <value> (source)
- Trailing P/E / Forward P/E / EV/EBITDA: <values>
- Net cash or debt: <value>
- One paragraph: what does the current valuation imply about market
  expectations?

{For commodities/crypto: spot price, 1Y/5Y range, key correlations.}
{For private: state "No public financials" and skip the block.}

## Scenario Analysis

{Base / Bull / Bear table. For equities, follow
`financial-analysis/references/scenario-modeling.md` — each scenario
specifies revenue, margin, multiple, implied share price. For
commodities/crypto, scenarios are price-path-driven instead.}

| Scenario | Revenue | Earnings | Multiple | Implied Price | Probability |
|---|---|---|---|---|---|
| Bull | ... | ... | ... | ... | ...% |
| Base | ... | ... | ... | ... | ...% |
| Bear | ... | ... | ... | ... | ...% |

## Peer Comparables

{3–5 curated peers (not the raw `yfinance peers` list). Table:}

| Ticker | Market Cap | Fwd P/E | EV/EBITDA | Rev Growth | Notes |
|---|---|---|---|---|---|

One paragraph: where does the target sit relative to peers, and what
does the gap (cheaper / richer) imply?

## Catalysts & Timeline

{Numbered list of upcoming events that could move the thesis, with dates
or windows. Earnings, product launches, regulatory decisions, macro
prints. For commodities: supply/demand inflection points, OPEC dates,
inventory releases.}

## Sentiment & Positioning

{Always present, even for boring names. 2–3 paragraphs covering:}
- **Direction of sentiment** — what `reddit-research` and `xai-search`
  surfaced. Tag each source: `[Reddit]`, `[X]`.
- **Crowdedness** — is the trade already crowded long/short? Is the
  bear case common knowledge?
- **Notable counter-takes** — quote the smartest dissent you found, with
  a link.

If sentiment is quiet/no signal, say so — that *is* the finding.

## Risks That Would Flip the Thesis

{2–4 specific, observable conditions. Not generic "macro downturn" —
something concrete like "Q3 gross margin below 65%" or "trial readout
delayed past Q2 2027". These are the things the user should watch.}

## Sources

{Full list of citations, tagged by source type:}
- [Title](URL) — [Web] what was found
- [Title](URL) — [Reddit] what was found
- [Title](URL) — [X] what was found
- [Title](URL) — [SEC] what was found
- [Title](URL) — [Other] what was found
```

## Notes

- **Skip empty sections.** If the subject is private, skip Financial
  Snapshot / Scenario / Peers and say once in the Setup section that the
  financial layer is unavailable.
- **One peer table only.** Don't repeat the table in Comparables and again
  in Sentiment with the same names.
- **Catalysts must be dated.** "Upcoming earnings" without a quarter is
  not a catalyst; it's a calendar entry.
