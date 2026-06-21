---
name: financial-analysis
description: "Use when asked to do financial analysis of a public company — valuation, scenario modeling, peer comparables, sensitivity analysis, what-if revenue/earnings impact. Trigger phrases: 'financial analysis of X', 'is X overvalued', 'what if X adds Y in revenue', 'how does X compare to peers', 'P/E impact', 'valuation scenarios'. Always reach for this when a research report mentions a public company and the user wants the financial layer."
portal_summary: "Valuation, scenarios, and peer comparison for a public company"
portal_starter: true
tools: attach
---

# Financial Analysis

Produce a structured financial analysis of a public company. The output is a
markdown report posted inline plus a PDF attachment for sharing.

> **When running as a sub-agent (under `delegate`):** the PDF/`attach` delivery
> step is owned by the orchestrator, not you. Return the markdown report /
> digest only — the orchestrator runs the fact-check gate before delivering.
> The harness enforces this by refusing to grant a sub-agent the `attach` tool
> via this skill's `tools:` unlock, so the delivery step will not be available
> to you in that context regardless.

This skill is the **orchestrator** — it tells you how to combine data from
the data skills (`yfinance`, `fred`, `sec-edgar`) and apply four core
analytical frameworks. The frameworks themselves live in `references/` so
this file stays focused on workflow.

## Prerequisite skills

Always load `yfinance`. Load `fred` whenever the analysis needs a discount
rate, FX, or macro context. Load `sec-edgar` when fundamentals matter
enough that you need authoritative numbers (anything load-bearing — e.g., a
scenario built around segment revenue should be cross-checked against the
10-K, not pulled from yfinance alone).

## Workflow

### Step 1 — Frame the question

Before fetching anything, write down (mentally or in a scratchpad):

- **The target company** (ticker, sector).
- **What's being asked** — current valuation? scenario impact? peer
  comparison? all three?
- **Key drivers** — what 2-3 numbers, if changed, would move the answer
  most? (e.g., "incremental drug revenue", "discount rate", "peer P/E").
- **Time horizon** — TTM only, NTM, or multi-year projection?

If the question is vague ("analyze Lilly"), ask one clarifying question
about what specifically the user wants the analysis to answer. Don't fish
for data without a question.

### Step 2 — Fetch the base data

Always pull these for the target:

```bash
python skills/yfinance/yfin.py profile <TICKER>
python skills/yfinance/yfin.py multiples <TICKER>
python skills/yfinance/yfin.py financials <TICKER> --period annual
```

Then add as needed:

- **Macro inputs** (`fred`) — `DGS10` for risk-free rate, `CPILFESL` for
  inflation context, sector-specific PPI if relevant.
- **Authoritative fundamentals** (`sec-edgar facts`) — when a yfinance
  number is load-bearing or looks off, cross-check via the company's
  XBRL facts (e.g., `--concept Revenues` or
  `RevenueFromContractWithCustomerExcludingAssessedTax`).
- **Peer set** (`yfinance peers`) — starting point only; refine to a
  curated list of 3-5 real comparables for the analysis. The auto peer
  list is rough.

### Step 3 — Apply the analytical frameworks

Pick the frameworks that match the question. Each has a reference doc with
the exact formulas, structure, and how-to-present guidance:

| Framework | When | Reference |
|---|---|---|
| **Scenario modeling** | "what if X adds $30B revenue", probabilistic outcomes | `references/scenario-modeling.md` |
| **Valuation multiples** | "is the P/E reasonable", "implied price at peer multiple" | `references/valuation-multiples.md` |
| **Peer comparables** | "how does X stack up against PFE/MRK/JNJ" | `references/peer-comparables.md` |
| **Sensitivity** | "what assumptions matter most", confidence ranges | `references/sensitivity.md` |

For a typical "what if X event" question, you usually want **all four**:
scenario sets the cases, multiples translate scenarios into share-price
implications, comparables ground the multiples in peer reality, and
sensitivity shows how robust the answer is.

Read each reference at the moment you apply it. They're short.

### Step 4 — Write the report

Always use this structure. Skip a section only if it would be empty.

```markdown
# Financial Analysis: <Company> (<TICKER>)
*Prepared <date>. Data as of <latest source date>.*

## Question
<one paragraph: what we're answering and why it matters>

## Assumptions & Data Sources
- <Each load-bearing number with its source — yfinance / SEC 10-K / FRED — and as-of date.>
- <Any estimate or assumption (especially user-supplied) explicitly flagged.>
- <Macro inputs used (rates, inflation), with FRED series IDs.>

## Current Position
- Market cap: <value> (yfinance, <date>)
- Revenue (TTM): <value> (source)
- Trailing P/E: <value>; Forward P/E: <value>; EV/EBITDA: <value>
- One paragraph of context: what does the current valuation imply?

## Scenario Analysis
<Base / Bull / Bear table with revenue, earnings, implied share price.
See references/scenario-modeling.md for structure.>

## Peer Comparables
<Table of 3-5 peers with current multiples; note where the target sits.
See references/peer-comparables.md.>

## Implied Valuation
<Apply peer multiples to scenario earnings to get implied price ranges.
See references/valuation-multiples.md for the calc.>

## Sensitivity
<Which 1-2 assumptions move the answer most; ±20% bands on each.
See references/sensitivity.md.>

## Bottom Line
<2-3 sentences. What the analysis says in plain English. Acknowledge the
biggest uncertainty.>
```

### Step 5 — Deliver

1. Write the markdown to `context/workspace/generated/<TICKER>-<YYYY-MM-DD>.md`.
2. Convert to PDF with pandoc:

   ```bash
   pandoc context/workspace/generated/<TICKER>-<DATE>.md \
     -o context/workspace/generated/<TICKER>-<DATE>.pdf
   ```
3. Attach the PDF: `attach(path="context/workspace/generated/<TICKER>-<DATE>.pdf")`.
   If pandoc fails, attach the `.md` as fallback.
4. In your reply, post the **Bottom Line** section verbatim plus the
   scenario table inline. The full report is the attachment.

## Honesty rules — non-negotiable

- **Cite every number with its source and as-of date.** No floating numbers.
- **Flag estimates and user-supplied assumptions.** A scenario built on
  "the user said $30B" is not the same as a scenario built on company
  guidance. Make the distinction visible in the assumptions block.
- **Acknowledge what you don't know.** If guidance is stale, if peers are
  imperfect, if the FX assumption is napkin — say so. A confident-sounding
  number with hidden weakness is the worst output.
- **Don't invent peers.** If yfinance returns no peers, pick from a
  manually-known list of large-caps in the same industry, and say "manual
  peer selection" in the assumptions block.
- **Don't extrapolate beyond what the frameworks support.** This skill
  intentionally omits DCF — relative valuation only. Don't fake an
  intrinsic-value estimate from multiples; call it an *implied* price.

## Common mistakes

- **Skipping Step 1.** Without a sharp question, the analysis sprawls and
  the report is shapeless. Frame first.
- **Pulling `info` from yfinance.** It floods the context. Stick to
  `profile` / `multiples` / `financials`.
- **Treating yfinance as authoritative for fundamentals.** It's a scrape.
  For load-bearing numbers, cross-check `sec-edgar facts`.
- **Auto-peers without curation.** The yfinance peer list is rough.
  Pick 3-5 real comparables manually for the report.
- **Forgetting the assumptions block.** Every reader's first question is
  "where did these numbers come from". Answer it up top.
- **Forgetting `attach()`** — the PDF must be attached, not just written
  to disk.
