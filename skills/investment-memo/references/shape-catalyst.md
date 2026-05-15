# Shape: Catalyst-Driven Memo

Use when the memo is anchored on a **single event** — a drug readout, a
product launch, a regulatory decision, an earnings print, a macro shift —
and analyzes its impact on one or more instruments (e.g., "retatrutide
blockbuster analysis", "who wins from rate cuts", "Nvidia GTC implications
for the AI supply chain").

The shared memo header (Date, Prepared for, Thesis, Status, Executive
Summary, Investment Thesis long-form) is defined in `SKILL.md`. The Thesis
line here states the *event-driven* view ("Long LLY into the 2027
retatrutide Phase 3 readout"). The long-form thesis argues why this event
matters and which side of it to be on.

## Body outline

```markdown
## The Catalyst

{2–3 paragraphs. What the event is, the mechanics, the timeline, and why
it matters. For a drug readout: trial design, primary endpoint, expected
patient counts, prior data. For a regulatory event: who decides, what
they're deciding, the precedents. For a macro shift: the trigger and the
transmission mechanism.}

Make the timeline explicit:
- **Expected date / window:** {e.g., "Q2 2027" or "FDA decision by
  PDUFA date 2026-08-15"}
- **Path dependencies:** {what has to happen first}
- **Recent updates:** {any news in the last 90 days that affected the
  base case}

## Affected Instruments Map

{Short table — which tickers the catalyst touches, and in which
direction.}

| Ticker | Name | Direction | Magnitude | Why |
|---|---|---|---|---|
| ... | ... | Winner | High / Medium / Low | One-line transmission |
| ... | ... | Loser | ... | ... |
| ... | ... | Mixed | ... | ... |

Magnitude is a qualitative read of how much the catalyst moves the
ticker, not a precise % estimate. Use a precise estimate only when you
have a defensible model for it.

## Per-Ticker Impact

{One subsection per affected name, ordered by magnitude. Slimmer than a
single-name memo — focus on the catalyst's transmission, not the full
business.}

### {Ticker} — {Name}

{Short pitch: why this ticker is exposed to the catalyst and which side
of it. One paragraph.}

**Base case impact:** {What happens if the catalyst plays out as
expected. Include a rough range — e.g., "+10–20% on a positive Phase 3
readout, driven by analyst model revisions toward $X peak sales".}

**Path dependencies:** {What else has to be true for the impact to land
— e.g., "assumes label includes obesity indication, not just T2D".}

**Valuation anchor:** {One load-bearing number with source — current
multiple, current implied sales contribution, current event probability
priced in.}

## Scenario Analysis

{Three scenarios for how the catalyst plays out. Unlike single-name
shape, the scenarios are event-driven (success / partial / fail), not
revenue-driven.}

| Scenario | What Happens | Affected-Tickers Reaction | Probability |
|---|---|---|---|
| Best case | {Event clears, label exceeds expectations} | Winners +X%, Losers -Y% | ...% |
| Base case | {Event clears, label in line} | Winners +X%, Losers -Y% | ...% |
| Worst case | {Event misses or delays} | Winners -X%, Losers +Y% | ...% |

State the *priced-in* scenario explicitly — what is the market currently
discounting? This is where the contrarian edge usually sits.

## Sentiment & Positioning

{Always present. 2–3 paragraphs:}
- **Pre-catalyst sentiment** — what `reddit-research` and `xai-search`
  show about expectations. Tag each: `[Reddit]`, `[X]`.
- **Crowdedness** — is everyone already long the winner / short the
  loser? Asymmetry in positioning is often the trade.
- **Notable counter-takes** — the smartest voice arguing the priced-in
  scenario is wrong.

## The Trade(s)

{Only present if verdict-mode is on. Specific trade expressions:}
- **Primary trade:** {Long / short on which ticker, with sizing context
  if user-mandate is known}
- **Pair / hedge:** {If the conviction is on the *spread* not the
  direction — long winner / short loser}
- **Optionality:** {If options pricing is informative — e.g., "implied
  vol on LLY into earnings suggests a ±X% move; the trade is cheap on
  the long side"}

Do not invent specific contract strikes or sizes. Frame the trade
qualitatively unless the user provided constraints.

## Risks & Path Dependencies

{3–5 specific risks that would invalidate the catalyst-driven thesis.
Include both event-side risks (the catalyst itself disappoints) and
transmission-side risks (the catalyst plays out but the ticker doesn't
respond as modeled).}

## Sources

{Tagged source list — same format as other shapes.}
```

## Notes

- **The catalyst is the spine.** Don't slip back into per-ticker
  single-name analysis. If a ticker needs that depth, the memo is
  probably a single-name memo, not a catalyst memo.
- **Be explicit about what's priced in.** Catalyst trades live or die on
  the gap between consensus expectations and reality.
- **Probability columns are calibration, not precision.** Round to
  10–20% buckets; spurious precision (37%) signals false confidence.
- **Path dependencies are first-class.** A drug clears Phase 3 but the
  label disappoints. A rate cut happens but the curve inverts further.
  Memo these alongside event-success risk.
