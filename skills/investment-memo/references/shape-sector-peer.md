# Shape: Sector / Peer-Ranking Memo

Use when the memo compares and ranks **multiple instruments** within a
sector, theme, or basket (e.g., "top 10 gold miners", "best biotech longs
for 2026", "rank the GLP-1 winners").

The shared memo header (Date, Prepared for, Thesis, Status, Executive
Summary, Investment Thesis long-form) is defined in `SKILL.md`. The
Thesis line here states the *thematic* view ("Long large-cap gold miners
into a weak-dollar regime"), and the long-form thesis argues the macro /
sector setup that makes the ranking matter.

## Body outline

```markdown
## Sector Setup

{2–3 paragraphs. What's happening in the sector, why now, what macro or
structural drivers make this thematically relevant. The reader needs to
understand why this ranking exists before they read the names.}

## Ranking Methodology

{Short — half a page max. State the criteria used to rank and weight:}
- **Quantitative inputs** — e.g., reserves/production for miners, R&D
  pipeline depth for biotech, AUM growth for asset managers. Specify the
  data source and as-of date for each.
- **Qualitative inputs** — management quality, balance-sheet strength,
  optionality. Make the lens explicit so the ranking is reproducible.
- **Weighting** — equal-weight, or skewed toward one criterion. State it.

If the user specified criteria, follow them. If not, make a choice and
own it.

## The Ranking

{Lead table — the headline deliverable. Keep it scannable.}

| Rank | Ticker | Name | Market Cap | Fwd P/E | EV/EBITDA | Key Pitch | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | ... | ... | ... | ... | ... | One-line thesis | Buy / Hold / Avoid |
| 2 | ... | ... | ... | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... | ... | ... |

The **Verdict** column only appears if verdict-mode is on for the
request. Otherwise, drop it.

## Per-Name Detail

{One subsection per ranked name. Keep each tight — this is a ranking, not
ten single-name memos. 1–3 paragraphs per name covering:}

### {Rank}. {Ticker} — {Name}

{One paragraph: the pitch. What this name does, why it ranks where it
does, the one-line thesis.}

{Optional: one paragraph on the bull / bear tension specific to this
name, if it differs from the sector view.}

{One line: load-bearing valuation point — e.g., "Trades at 12x FY26 EPS
vs. peer median 18x ([yfinance](URL), 2026-MM-DD)."}

## Cross-Cutting Risks

{Risks that affect the entire ranking, not just one name. 3–5 specific
items with concrete signals — e.g., "Gold below $1,900/oz invalidates
margin assumptions across the basket" or "FDA priority-review pause
delays catalysts for the top 3 ranked biotechs".}

## Sentiment & Positioning

{Aggregate across the ranked names. 2–3 paragraphs:}
- **Sector-wide sentiment** — what `reddit-research` and `xai-search`
  surfaced about the theme. Tag each source: `[Reddit]`, `[X]`.
- **Crowdedness** — is the sector already a consensus long/short?
- **Standouts** — which names in the ranking are getting outsized
  positive or negative chatter relative to fundamentals?

## Top Picks / Avoids

{Only present if verdict-mode is on. 2–3 lines:}
- **Top long(s):** {Ticker(s)} — {one-line why}
- **Top short(s) / avoid(s):** {Ticker(s)} — {one-line why}

## Sources

{Tagged source list — same format as single-name shape. Sector reports,
peer data, SEC filings, social citations.}
```

## Notes

- **Don't write a full single-name memo per ranked name.** A sector memo
  with ten three-page sub-memos is a deep-research report, not a ranking.
  Keep per-name sections to ~1–3 short paragraphs.
- **Be honest about methodology.** Readers will judge the ranking by the
  criteria. Making them implicit invites accusations of cherry-picking.
- **Skip per-name detail for names that didn't make the cut.** Mention
  notable also-rans in one line under the table — don't section them.
- **Sentiment is aggregate, not per-name.** Ten sentiment paragraphs
  would dwarf the rest of the memo.
