---
name: investment-memo
description: "Use when asked to build, back, pitch, or stress-test an investment thesis on any investable subject — public equity, ETF, sector, commodity, crypto, or private name. Also use for any request that produces buy/sell/hold recommendations or ranks investable names by return potential. Trigger phrases: 'investment thesis on X', 'investment memo', 'investment research on X', 'should I buy/short X', 'is X a buy', 'bull case', 'bear case', 'base case', 'steelman/strawman this trade', 'pitch X', 'kill this trade', 'top N [sector] to own', 'top N [sector] by potential/upside', 'recommendations on [stocks/sector]', 'rank/screen [names] by upside/potential', 'which [stocks] to buy', 'who wins from [catalyst]', 'is the trade crowded', 'blockbuster analysis', 'long/short X'. Use this — not deep-research — whenever the deliverable is a recommendation or ranking of investables, even if the request says 'research'. Confirms a memo plan with you before running the research, financial, and sentiment phases. Produces a fact-checked PDF memo that composes deep research, financial analysis, and social sentiment under a directional view."
portal_summary: "Fact-checked investment memo on any stock, sector, or asset"
portal_starter: true
tools: attach
---

# Investment Memo

Produce a structured investment memo combining **deep research**, **financial
analysis**, **social sentiment**, and an **independent fact-check** under a
single directional view. The output is a PDF attachment with a memo-style
header (Date, Prepared for, Prepared by, Thesis, Status) and a body whose
structure follows the analysis, not a fixed template.

This skill is the **orchestrator** — it does not re-implement research, data
fetching, or fact-checking. It loads the underlying skills, sequences them,
frames the analytical work, and gates delivery on fact-check.

## Skills this memo composes

The orchestrator loads only **`deep-research`** — enough to frame the
research phase and brief the sub-agents. The other skills run inside
sub-agents, which load them themselves:

- `deep-research` — research phase
- `financial-analysis` — financial layer; pulls in `yfinance`, `fred`,
  `sec-edgar` as needed
- `reddit-research` + `xai-search` — community and X/Twitter sentiment.
  Sentiment is part of every memo even when the request doesn't mention it.
- `fact-checker` — never loaded inline; always invoked via `delegate` with
  a fresh context window (Step 6)

## Delegation model

`delegate` is **synchronous** — it buys no parallelism and no speedup. Its
only value is **context isolation**: a sub-agent burns through raw tool
output (web-fetch bodies, JSON dumps, search hits) in its own context
window and returns a compact digest, keeping that noise out of yours.
Decide where to delegate on that basis, not by rote.

The research, financial, and sentiment phases each generate far more raw
output than the digest you need back — they are the natural things to
delegate. A trivial lookup (one ticker's `profile`/`multiples`, resolving a
ticker symbol) is a couple of small `bash` calls — run it inline; a
sub-agent there saves no context and costs a whole agent loop.

Each sub-agent runs under a hard ~300s timeout. The real judgment call is
sizing: a single delegate spanning research **and** a 10-ticker financial
pull **and** sentiment will exceed 300s and lose everything it gathered.
Keep each delegate to one coherent chunk of work; if one times out, narrow
the scope rather than retrying it unchanged.

## Workflow

### Step 1 — Frame the question

Before fetching anything, write down (mentally or in scratchpad):

- **The instrument(s)** — ticker, ETF, commodity proxy, crypto symbol,
  private name. If private/non-public, note that the financial layer will
  be thin and the memo will lean harder on the research/sentiment layers.
- **Define the universe — and own the definition.** When the request
  names a category ("gold miners", "biotech longs"), every name you
  analyze or rank must genuinely belong to that category. A royalty/
  streaming company is not a miner; an ETF is not a single stock; a
  mid-tier is not a top-10 producer. If you filter the set for practical
  reasons — tradability, ADR availability, liquidity — that is a judgment
  call you must **surface, not bury**: state the filter explicitly in the
  memo, and still name the true category members it excluded, one line
  each. Never silently swap a real member out for a more convenient name.
- **What the request is asking for** — argue a thesis? decide a position?
  steelman/strawman? rank a sector?
- **Verdict mode** — does the request *imply a decision*? Examples:
  - Verdict required: "should I buy NVDA", "is X a buy", "long or short",
    "buy/avoid/pass", "kill this trade", "pitch X".
  - No verdict: "steelman the bear case", "strawman the bull thesis",
    "lay out the cases", "thesis on X" (open framing).

**Analytical lens.** Most requests fall into one of three shapes — a
**single name** (one instrument is the subject), a **sector ranking**
(multiple names compared and ranked), or a **catalyst** (one event drives
the memo across one or more names). This is a lens, not a template: it
tells you which moves to emphasize — a ranking leans on per-name
comparison, a catalyst on the event and its winners and losers — but the
memo's sections still emerge from the analysis (Step 5), never from a
fixed skeleton. If the request is genuinely ambiguous about what it
wants, ask one clarifying question.

### Step 1.5 — Present memo plan & confirm

Before delegating anything, present the memo plan to the user using the
exact template below, **then end the turn**. Do not call `delegate`,
`financial-analysis`, `reddit-research`, `xai-search`, or any data-fetching
`bash` call (yfinance, fred, sec-edgar) in the same turn the plan is
presented. The plan turn is text-only. Wait for the user to reply.

This is where the **universe definition, applied filters, and excluded
names** from Step 1 get surfaced for the user to sanity-check — before
sub-agents burn context fetching data on the wrong set. Step 5's
universe-integrity language assumes this gate already caught the obvious
mismatches; the fact-check (Step 6) will not.

**Plan template** — emit inline as your text reply, not as an attachment:

```markdown
**Memo plan**

**Understanding:** {one-sentence restatement of what you think the user is asking, in your own words}

**Instrument(s) & universe:** {tickers / ETF / commodity proxy / private name(s)}. If a category is named ("gold miners", "biotech longs"), list the names you'll cover, the filters you applied (tradability, liquidity, ADR availability, market cap floor, etc.), and the **true category members you're excluding** under those filters — one line each. If single-name, just name the instrument.

**Shape:** {single-name | sector ranking | catalyst}

**Verdict mode:** {Required — Buy/Sell/Avoid/Pass/Hold | Not required — framing only (e.g. steelman, lay out the cases)}

**Sub-questions → phases:**
1. {Setup / what is the business or event} → deep-research
2. {Bull case} → deep-research
3. {Bear case} → deep-research
4. {Load-bearing numbers — valuation, peers, scenarios} → financial-analysis ({slim per-name | full workflow})
5. {Positioning & sentiment — crowdedness, counter-takes} → reddit-research + xai-search
{...}

**Deliverable:** {what you'll hand back — e.g. "Fact-checked PDF memo with shared header, executive summary, body covering setup/bull/bear/what-the-market-is-missing/load-bearing numbers{/per-name coverage}, sentiment & positioning, {verdict if applicable}, inline numbered citations, Sources, and a Fact-Check Addendum."}

Does this look right, or would you like me to adjust the universe (add/drop names, change filters), swap a sub-question, change shape or verdict mode, or narrow the financial depth before I start?
```

**After emitting the plan, stop.** No tool calls. The next turn belongs
to the user.

**When the user replies:**
- "Looks good" / "go" / "yes" / 👍 → proceed to Step 2 with the plan as-is.
- "Drop X" / "add Newmont" / "no verdict" / "broader universe" / "skip
  the financial layer" / etc. → ack the change, revise the plan, re-emit
  the updated version, and stop again to re-confirm. **Never start
  research on a half-confirmed plan** — one more round of clarification
  is cheaper than several minutes of sub-agent context spent on the
  wrong universe.
- A question about the plan → answer it, then re-emit the plan if you
  changed anything material, and stop.

**Skip conditions** — skip Step 1.5 entirely and go straight to Step 2
only when:

1. **The user explicitly opted out** in this turn or the immediately
   preceding one — phrases like "skip the plan", "just go", "don't ask,
   just write the memo", "no preview". Treat these as durable for the
   current memo only.
2. **The original prompt already specified all five:** instrument(s),
   universe (single name or an unambiguous set — no category interpretation
   needed), shape, verdict-mode, and scope. A prompt like *"Buy/sell call
   on NVDA on a 12-month horizon, verdict required"* qualifies — one
   instrument, universe is trivially the single name, shape is single-name,
   verdict is required, scope is bounded. *"Thesis on gold miners"* does
   not — the universe (which miners? majors only? streamers in or out?),
   shape (ranking or single-name pick?), and verdict mode are all open.
   When in doubt, present the plan; universe integrity matters more here
   than in plain research, so this bar is intentionally higher than
   `deep-research-guided`'s.

If you skip, say so in one line ("Scope is clear — going straight to the
memo.") so the user knows the gate is bypassed and can interrupt if you
misjudged.

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

Capture a source URL for every claim as you research — keep a running
list. The memo cites them with numbered inline markers (see Step 5) and
the fact-checker needs them.

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

The memo has two parts: a fixed **shared header** (below) and a body whose
structure **emerges from the analysis** — there is no section skeleton to
fill in. Every memo, regardless of shape, opens with the shared header:

```markdown
# {Long-form descriptive title — magazine-cover style, includes ticker(s)}
## {Subtitle framing the question, angle, or directional view}

**Date:** {Month DD, YYYY}

**Prepared for:** {user's name from context/identity.md — omit line if unknown}

**Prepared by:** {agent's name from context/identity.md — omit line if unknown}

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
it. Cite load-bearing facts inline with numbered markers (see Inline citations).}
```

After the shared header, write the body. It has no prescribed section
outline — structure it however the analysis demands — but it must cover
every one of these analytical moves:

- **The setup** — the business, instrument, or event, and why it matters now.
- **The bull case** — the strongest evidence for the directional view.
- **The bear case** — what would invalidate the thesis.
- **What the market is missing** — the non-consensus angle, if any.
- **The load-bearing numbers** — valuation, peers, catalyst dates, each
  with a source and an as-of date.
- **Per-name coverage** (ranking shape) — for *every* ranked name, cover
  its **major projects, pipeline, and catalysts**. A multi-billion-dollar
  asset must not go unmentioned because a name got a thin paragraph.
- **The verdict** — only if verdict-mode is on (see Verdict logic).

**Comparison tables.** A ranking usually benefits from a comparison
table — but build it *after* you have chosen the names. The table
displays the names you selected; it does not select them. If a name
lacks a metric (state-owned, foreign-listed, no public multiples), leave
the cell blank or mark it n/a — never drop a real category member
because a column would be empty. Keep any table to roughly five or six
columns so it fits a typeset page (see Step 7).

**Inline citations and the Sources section.** The memo ends with a
`## Sources` section, and every claim in the body carries a clickable
numbered marker that jumps to it — same convention as `deep-research`
(see its "Inline citations" block). In short:

- Body marker, immediately after the claim: `…peak sales near $5B.[^2^](#src-2)`
- Sources entry, anchored by number:

  ```markdown
  ## Sources

  1. []{#src-1}[Title](URL) — what was found here
  2. []{#src-2}[Title](URL) — what was found here
  ```

Number in first-appearance order, reuse a number when a source recurs,
repeat the marker for multiple sources on one claim. Every marker needs
exactly one matching `#src-N` anchor. The `## Fact-Check Addendum` (Step 6)
goes *after* this `## Sources` section.

Write the markdown to `workspace/generated/{ticker-or-slug}-{YYYY-MM-DD}.md`.

**Honesty rules** (lifted from `financial-analysis` — non-negotiable):

- Cite every number with a numbered marker, source, and as-of date. No floating numbers.
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

**The fact-check verifies stated claims — not omissions or category
errors.** It will catch a wrong number; it will not catch a missing
asset, a name that doesn't belong in the category, or a silently
narrowed universe. Completeness and universe-integrity are *your* job
(Steps 1 and 5) — do not rely on this pass to save them.

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

For drafts >50KB, write to `workspace/scratch/memo-{slug}-{date}.md`
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

Convert the fact-checked markdown to PDF with plain pandoc — the same
LaTeX-via-pandoc path `deep-research` uses, which produces a cleanly
typeset document:

```bash
pandoc workspace/generated/{slug}-{date}.md -o workspace/generated/{slug}-{date}.pdf
```

Do **not** render via HTML, headless Chromium, weasyprint, or a CSS
stylesheet — that route produces a cheap-looking web-page printout
instead of a typeset memo. If a comparison table is too wide to fit the
page, the fix is a narrower table (Step 5), not a different renderer.

Attach the PDF: `attach(path="workspace/generated/{slug}-{date}.pdf")`. If
pandoc fails, attach the `.md` as fallback.

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

## Examples

**Single-name verdict** — user asks: *"Pitch me TSLA — should I buy on
a 12-month horizon?"*

*Turn 1 (plan):* the prompt names the instrument (TSLA) and the
verdict-mode (Buy/Sell/Avoid required) but leaves the shape and scope
open. Universe is trivially TSLA, but the framing — robotaxi catalyst?
auto fundamentals? energy story? — isn't pinned. Emit the plan:

> **Memo plan**
>
> **Understanding:** You want a directional Buy/Sell/Avoid call on TSLA over a 12-month horizon, with the case anchored on what's likely to drive the stock over that window.
>
> **Instrument(s) & universe:** TSLA (single name). No peer ranking; Tesla peers (RIVN, BYD, GM) will appear in the multiples comparison but not as ranked alternatives.
>
> **Shape:** single-name
>
> **Verdict mode:** Required — Buy / Sell / Avoid + confidence
>
> **Sub-questions → phases:**
> 1. Setup — auto, energy, FSD/robotaxi, where revenue and gross profit actually come from today → deep-research
> 2. Bull case — robotaxi/FSD optionality, energy storage scaling, margin recovery → deep-research
> 3. Bear case — auto demand softness, margin compression, FSD/robotaxi disappointments → deep-research
> 4. Load-bearing numbers — scenario model (bull/base/bear FY revenue + margin), peer multiples vs. auto and big-tech baskets, current valuation → financial-analysis (full workflow)
> 5. Positioning & sentiment — is the long crowded, what's the bear consensus → reddit-research + xai-search
>
> **Deliverable:** Fact-checked PDF memo with shared header, executive summary closing in a Buy/Sell/Avoid + confidence, setup/bull/bear/what-the-market-is-missing/scenario + multiples, sentiment & positioning, inline numbered citations, Sources, Fact-Check Addendum.
>
> Does this look right, or would you like me to adjust the framing, swap a sub-question, or narrow the financial depth before I start?

Stop. No tool calls.

*Turn 2:* user replies *"drop the energy story, keep it focused on auto
fundamentals and FSD"* → ack, revise sub-questions 1 and 2 (remove
energy strand), re-emit the plan, stop.

*Turn 3:* user replies *"go"* → proceed to Step 2.

**Sector ranking** — user asks: *"Top 5 gold miners to own going into 2027."*

*Turn 1 (plan):* category is named ("gold miners") but the universe
isn't — streamers? majors only? Western-listed? Verdict-mode (ranking +
top-N picks) is implied but the scope of names to consider needs to be
surfaced. Emit the plan:

> **Memo plan**
>
> **Understanding:** You want a ranked top-5 of gold miners to own with a 2027 horizon, with the picks defended on fundamentals + catalysts and the also-rans explained.
>
> **Instrument(s) & universe:** Gold producers ranked by upside. Candidate set: Newmont (NEM), Barrick (GOLD), Agnico Eagle (AEM), Kinross (KGC), Gold Fields (GFI), AngloGold Ashanti (AU), Northern Star (NST.AX), Endeavour Mining (EDV.TO). **Filters applied:** primary-listing tradability and >$3B market cap. **True category members excluded under those filters:** Polyus (Russian-listed, sanctions), Zijin (HK-listed, ADR illiquid), several mid-tiers below the cap. **Royalty/streaming names (Franco-Nevada, Wheaton, Royal Gold) are NOT in the set** — they aren't producers; flagging here so you can override if you want them included.
>
> **Shape:** sector ranking
>
> **Verdict mode:** Required — top-5 ranked, with one-line verdict per name (Own / Watch / Pass)
>
> **Sub-questions → phases:**
> 1. Sector setup — gold price regime, cost curve, M&A backdrop → deep-research
> 2. Per-name fundamentals & catalysts — AISC trajectory, reserve life, major projects, near-term catalysts → deep-research
> 3. Bull/bear cases at the sector level → deep-research
> 4. Per-name financial pass — multiples, slim scenario, peer table → financial-analysis (slim per-name across the candidate set)
> 5. Positioning & sentiment — retail/X attention on the names, crowdedness → reddit-research + xai-search
>
> **Deliverable:** Fact-checked PDF memo with shared header, executive summary closing in a ranked top-5 + Own/Watch/Pass, per-name coverage (projects/pipeline/catalysts), sector-level bull/bear, comparison table, sentiment & positioning, inline numbered citations, Sources, Fact-Check Addendum.
>
> Does this look right, or would you like me to adjust the universe (add streamers? drop the market-cap floor? include Polyus anyway?), change filters, or narrow the financial depth before I start?

Stop.

*Turn 2:* user replies *"include royalty names, and bump it to top-7"*
→ ack, expand candidate set to include FNV/WPM/RGLD, update shape note
to allow streamers in the ranking, change top-5 → top-7 throughout the
plan, re-emit, stop.

*Turn 3:* user replies *"good"* → proceed to Step 2.

## Common mistakes

- **Skipping the plan gate on a vague request.** The default is confirm,
  not infer. Unless the prompt already pinned down instrument(s),
  universe, shape, verdict-mode, and scope (or the user explicitly opted
  out), present the Step 1.5 plan and stop. The cost of one extra turn
  is small; the cost of researching the wrong universe — or letting a
  silently-narrowed set ride through to fact-check — is several minutes
  of wasted sub-agent context.
- **Starting research in the same turn the plan was presented.** The plan
  turn is text-only. No `delegate`, `financial-analysis`,
  `reddit-research`, `xai-search`, or `bash` data calls. The user's reply
  is the trigger for Step 2.
- **Silently redefining the universe.** Swapping the category the user
  named ("miners") for a more convenient set ("Western-tradable names")
  without disclosing it. State every filter; list the true members you
  dropped.
- **Mixing instrument categories in one ranking.** A royalty/streaming
  company in a "miners" list, an ETF among single stocks. The ranked set
  must match the named category.
- **Missing a ranked name's major assets.** Every name in a ranking gets
  its major projects, pipeline, and catalysts covered — a thin paragraph
  is no excuse to omit a multi-billion-dollar asset.
- **Letting a table's columns decide who's eligible.** Build the table
  after choosing the names; blank cells are fine, dropped names are not.
- **Rendering the PDF via HTML/Chromium/CSS.** Use plain pandoc (LaTeX) —
  the HTML route looks cheap. Narrow a wide table instead.
- **Skipping sentiment because "the name is boring".** Always include —
  "no signal" is itself a finding.
- **Fact-checking yourself.** Always `delegate`. Your reasoning is anchored
  on the same framing you used to write.
- **One mega-delegate for the whole memo.** Research + financials +
  sentiment in a single `delegate` exceeds the 300s budget and loses all of
  it on timeout. Delegate bulky phases separately — see "Delegation model".
- **Delegating trivial lookups.** A sub-agent to fetch one ticker's
  multiples or resolve a symbol saves no context and costs a full agent
  loop. Inline it.
- **Inventing a verdict for steelman/strawman requests.** The framing is
  the deliverable; a verdict would defeat the purpose.
- **Treating Reddit/X opinions as facts.** Sentiment is signal, not a
  citation for a number.
- **Floating numbers.** Every number gets a source and an as-of date in
  the Assumptions block.
- **Bare URLs instead of numbered markers.** Claims cite sources with
  `[^N^](#src-N)` markers resolving to the `## Sources` list — not raw
  inline URLs and not nothing.
- **Marker/anchor mismatch.** Every `[^N^](#src-N)` marker needs exactly
  one matching `[]{#src-N}` anchor in Sources, and every Sources entry must
  be cited. A marker with no anchor renders as a dead link in the PDF.
- **Attaching .md instead of .pdf.** Always render PDF first; only fall
  back if pandoc fails.
- **Forgetting `attach()`.** The PDF must be attached, not just written
  to disk.
- **Header lines without blank lines between them.** Each `**Field:**`
  line in the header needs a blank line before the next, or pandoc renders
  them as a run-on paragraph.
