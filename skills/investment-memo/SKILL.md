---
name: investment-memo
description: "Use when asked to build, back, pitch, or stress-test an investment thesis on any investable subject — public equity, ETF, sector, commodity, crypto, or private name. Also use for any request that produces buy/sell/hold recommendations or ranks investable names by return potential. Trigger phrases: 'investment thesis on X', 'investment memo', 'investment research on X', 'should I buy/short X', 'is X a buy', 'bull case', 'bear case', 'base case', 'steelman/strawman this trade', 'pitch X', 'kill this trade', 'top N [sector] to own', 'top N [sector] by potential/upside', 'recommendations on [stocks/sector]', 'rank/screen [names] by upside/potential', 'which [stocks] to buy', 'who wins from [catalyst]', 'is the trade crowded', 'blockbuster analysis', 'long/short X'. Use this — not deep-research — whenever the deliverable is a recommendation or ranking of investables, even if the request says 'research'. Produces a fact-checked PDF memo that composes deep research, financial analysis, and social sentiment under a directional view."
tools: attach
---

# Investment Memo

Produce a structured investment memo combining **deep research**, **financial
analysis**, **social sentiment**, and an **independent fact-check** under a
single directional view. The output is a PDF attachment with a memo-style
header (Date, Prepared for, Prepared by, Thesis, Status) and a section
outline that adapts to the request's shape.

This skill is the **orchestrator** — it does not re-implement research, data
fetching, or fact-checking. It loads the underlying skills, sequences them,
applies the right outline template, and gates delivery on fact-check.

## Prerequisite skills

Always load (in this order):

1. `deep-research` — drives the research phase
2. `financial-analysis` — drives the financial layer (which itself pulls in
   `yfinance`, `fred`, `sec-edgar` as needed)
3. `reddit-research` — community sentiment
4. `xai-search` — X/Twitter real-time sentiment
5. `fact-checker` — **never** loaded inline; always invoked via `delegate`
   from Step 6 with a fresh context window

Sentiment is part of every memo — load `reddit-research` and `xai-search`
even when the request doesn't mention sentiment.

## Workflow

### Step 1 — Frame the question and detect shape

Before fetching anything, write down (mentally or in scratchpad):

- **The instrument(s)** — ticker, ETF, commodity proxy, crypto symbol,
  private name. If private/non-public, note that the financial layer will
  be thin and the memo will lean harder on the research/sentiment layers.
- **What the request is asking for** — argue a thesis? decide a position?
  steelman/strawman? rank a sector?
- **Verdict mode** — does the request *imply a decision*? Examples:
  - Verdict required: "should I buy NVDA", "is X a buy", "long or short",
    "buy/avoid/pass", "kill this trade", "pitch X".
  - No verdict: "steelman the bear case", "strawman the bull thesis",
    "lay out the cases", "thesis on X" (open framing).
- **Shape** — pick exactly one. Each shape has an outline template in
  `references/`. Read the matching template before drafting (Step 5).

| Shape | When | Template |
|---|---|---|
| **Single-name** | One instrument is the subject ("thesis on NVDA", "is LLY a buy") | `references/shape-single-name.md` |
| **Sector / peer ranking** | Multiple instruments compared and ranked ("top 10 gold miners", "best biotech longs") | `references/shape-sector-peer.md` |
| **Catalyst** | One event drives the memo, may touch multiple tickers ("retatrutide blockbuster", "who wins from rate cuts") | `references/shape-catalyst.md` |

If the request is ambiguous about shape, ask one clarifying question. Don't
guess between shapes — they produce visibly different memos.

### Step 2 — Research phase (deep-research)

Follow the `deep-research` workflow: decompose into 3–5 sub-questions
tailored to the shape, pick sources, search and read.

Sub-questions for an investment memo always include some version of:

- **What is the setup** — business / instrument / event background.
- **What's the bull case** — the directional argument's strongest evidence.
- **What's the bear case** — what would invalidate the thesis.
- **What's the market missing** — non-consensus angle, if any.
- **What are the load-bearing numbers** — revenue, margin, market size,
  peer multiples, catalyst dates. These feed Step 3.

Cite every claim with a URL inline as you research. The memo will need
these for the fact-checker.

### Step 3 — Financial phase (financial-analysis)

Run only when the subject has financial data. Decision tree:

- **Public equity / ETF** → full `financial-analysis` workflow: pull
  `profile`, `multiples`, `financials`, apply the relevant frameworks
  (scenario, multiples, peers, sensitivity). For a thesis memo you usually
  want scenario + peers + multiples; skip sensitivity if not load-bearing.
- **Commodity / FX** → use the `financial-analysis` data skills but only
  the parts that apply (spot price history via `yfinance`, macro context
  via `fred`). Scenario modeling against a commodity price band is often
  enough. No balance-sheet frameworks.
- **Crypto** → similar to commodity. `yfinance` works for major tickers
  (e.g. `BTC-USD`). No SEC layer.
- **Private** → no financial layer. State this explicitly in the
  Assumptions block: "No public financials; analysis relies on research
  and sentiment only." Move on.

For sector / peer-ranking shape, run a slim financial pass on each ticker
in the ranking (multiples + one-line scenario) rather than four full
analyses — depth would bloat the memo.

### Step 4 — Sentiment phase (reddit + X)

Always include. Even for boring B2B names, a one-paragraph "no signal
found" result is itself a finding (the trade isn't crowded).

- `reddit-research` → 2–4 targeted searches on relevant subreddits
  (`r/investing`, `r/stocks`, `r/wallstreetbets`, `r/biotechplays`,
  `r/CommercialRealEstate`, etc., depending on subject).
- `xai-search` → `x_search` on the ticker, the company name, and the
  catalyst (if relevant). Look for analyst chatter, insider commentary,
  meme/retail attention.

Capture:
- **Direction of sentiment** — bullish / bearish / mixed / quiet.
- **Crowdedness** — is everyone already long? is the bear thesis common
  knowledge?
- **Notable counter-takes** — the smartest voice on the other side of the
  consensus.

This becomes the **Sentiment & Positioning** section of the memo.

### Step 5 — Assemble the draft

Open `references/shape-{single-name,sector-peer,catalyst}.md` for the
section outline. Every memo, regardless of shape, opens with the **shared
header**:

```markdown
# {Long-form descriptive title — magazine-cover style, includes ticker(s)}
## {Subtitle framing the question, angle, or directional view}

**Date:** {Month DD, YYYY}

**Prepared for:** {user's name from context/identity.md — omit line if unknown}

**Prepared by:** {agent's name from context/identity.md, or "Curunir Investment Research"}

**Subject:** {One- to two-sentence framing — instrument(s), hypothesis, scope}

**Thesis (one line):** {The directional view in a single sentence}

**Status:** Draft — not yet independently fact-checked

---

## Executive Summary

{3–5 short paragraphs. Lead with the headline thesis in one sentence. Then:
strongest evidence for, strongest evidence against, key risks that would flip
the view, bottom-line "so what". Front-load the most important caveat.
If verdict-mode is on, the closing line states **Buy / Sell / Avoid / Pass /
Hold** + confidence (Low / Medium / High) + the 1–2 risks-that-would-flip-it.}

## Investment Thesis (long form)

{2–4 paragraphs. The fuller argument: setup, why now, what the market is
missing, what has to be true for the thesis to play out, what would invalidate
it. Cite load-bearing facts inline with URLs.}
```

After the shared header, the body follows the shape's outline. Write the
markdown to `workspace/memos/{ticker-or-slug}-{YYYY-MM-DD}.md`.

**Honesty rules** (lifted from `financial-analysis` — non-negotiable):

- Cite every number with source and as-of date. No floating numbers.
- Flag estimates and user-supplied assumptions.
- Acknowledge what you don't know — stale guidance, imperfect peers, no
  public financials. A confident-sounding number with hidden weakness is
  the worst output.
- Treat Reddit/X content as **sentiment signal, not facts**. Cross-reference
  factual claims against authoritative sources.

### Step 6 — Fact-check (default, not optional)

After the markdown draft is complete, before rendering the PDF, delegate
the fact-check to a fresh sub-agent. You cannot fact-check yourself — your
context is anchored on the same sources you used to write.

```python
delegate(task="""
Fact-check the investment memo below. Load the `fact-checker` skill and
follow the "Sub-agent workflow" section exactly. Return the structured
report as your final response.

<<<CONTENT_TO_FACT_CHECK
[paste the full draft markdown here, OR write to disk and provide path]
CONTENT_TO_FACT_CHECK>>>
""")
```

For drafts >50KB, write to `workspace/fact-check/memo-{slug}-{date}.md`
first and give the sub-agent the path.

When the sub-agent returns:

1. Apply each ❌ Contradicted and ⚠️ Partially accurate correction inline
   in the body. Do not leave wrong numbers in the prose.
2. Update the **Date** line to `{original date} (updated {date} — fact-checked, corrected & expanded)`.
3. Update the **Status** line to `Fact-checked {YYYY-MM-DD} — corrections incorporated`.
4. Append a `## Fact-Check Addendum` section after Sources, with a table
   summarizing what changed (same format as `deep-research` uses).

**Skip the fact-check only if** the user explicitly opts out ("skip
fact-check"). Sentiment-only opinion pieces are not a valid skip reason for
this skill — investment memos always have verifiable load-bearing claims
(prices, multiples, dates, peer numbers).

**If `delegate` times out** (`Sub-agent timed out after 300s`), do not
retry. Set Status to `Draft — fact-check timed out; scoped follow-up
recommended`, deliver as-is, and tell the user in the text reply that a
scoped fact-check (e.g., "verify the valuation numbers only") would
complete.

### Step 7 — Deliver

Convert the fact-checked markdown to PDF:

```bash
pandoc workspace/memos/{slug}-{date}.md \
  -o workspace/memos/{slug}-{date}.pdf
```

Attach the PDF: `attach(path="workspace/memos/{slug}-{date}.pdf")`. If
pandoc fails, attach the `.md` as fallback. Never convert to HTML.

In the text reply, post the **Executive Summary** verbatim plus one line on
whether the fact-check found material corrections. The full memo is the
attachment.

## Verdict logic

Apply only when the request implies a decision (see Step 1). The verdict
line lives in the closing of the Executive Summary, never invented from
thin air.

| Verdict | Use when |
|---|---|
| **Buy** | Thesis is supported, asymmetric upside, near-term catalysts present |
| **Sell / Short** | Thesis is invalidated or bear case dominates |
| **Avoid** | Not enough conviction in either direction; better names exist |
| **Pass** | Outside the user's stated mandate/risk tolerance (only if you know it) |
| **Hold** | Position implied to exist already; thesis intact but no new entry |

Confidence (Low / Medium / High) reflects the quality of the data and the
fact-check, not your enthusiasm. If `sec-edgar` was unavailable or
fact-check timed out, confidence drops.

## Common mistakes

- **Skipping shape detection.** Single-name and sector outlines are visibly
  different; squeezing a sector ranking into a single-name skeleton produces
  a shapeless memo. Read the template before drafting.
- **Skipping sentiment because "the name is boring".** Always include —
  "no signal" is itself a finding.
- **Fact-checking yourself.** Always `delegate`. Your reasoning is anchored
  on the same framing you used to write.
- **Inventing a verdict for steelman/strawman requests.** The framing is
  the deliverable; a verdict would defeat the purpose.
- **Treating Reddit/X opinions as facts.** Sentiment is signal, not a
  citation for a number.
- **Floating numbers.** Every number gets a source and an as-of date in
  the Assumptions block.
- **Attaching .md instead of .pdf.** Always render PDF first; only fall
  back if pandoc fails.
- **Forgetting `attach()`.** The PDF must be attached, not just written
  to disk.
- **Header lines without blank lines between them.** Each `**Field:**`
  line in the header needs a blank line before the next, or pandoc renders
  them as a run-on paragraph.
