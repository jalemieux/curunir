---
name: catalyst-memo
description: "Use when the seed is a forward-looking catalyst, event, rumor, or buzz item — not a ticker — and the user wants both a deep understanding of the catalyst AND its investment implications. Trigger phrases: 'I heard about X', 'what's the deal with [drug/product/event] X', 'investigate the buzz around X', 'deep dive on X and what it means for [companies/stocks]', 'is X for real', 'TAM impact of X', 'who wins/loses if X happens', 'how would X affect [ticker]'. Examples of catalysts: a new drug in trials, a product launch, a regulatory ruling, an antitrust case, a macro print, an M&A rumor, a geopolitical event. Use this — not investment-memo — when the user's starting point is the *event*, not the *instrument*. Use this — not deep-research-guided — when the deliverable must include scenario-priced stock/revenue implications and verdicts on the impacted names. Composes deep-research, financial-analysis, social sentiment (reddit + X), prediction-market probabilities (polymarket), and key-podcast commentary into a fact-checked PDF memo."
portal_summary: "Catalyst-first investment memo — start from an event/rumor, end with a fact-checked thesis on who wins"
portal_starter: true
tools: attach
---

# Catalyst Memo

Produce a structured investment memo whose **starting point is a catalyst**
(event, rumor, drug, product, ruling, macro print) rather than an instrument
(ticker, ETF). The output is a PDF attachment with the same memo-style header
as `investment-memo`, plus two forward-looking layers that `investment-memo`
does not cover: **prediction-market probabilities** and **key-podcast
commentary**.

This skill is an **orchestrator** — it does not re-implement research,
financial data, or fact-checking. It loads underlying skills, sequences them,
frames the catalyst-to-instruments mapping, and gates delivery on fact-check.

## How this differs from neighboring skills

- **`deep-research-guided`** — produces a research report. No financial
  layer, no verdict, no instrument scenarios. Use that skill when the user
  wants to *understand* something, not *bet* on it.
- **`investment-memo`** — instrument-first. User names a ticker; that skill
  builds the thesis. Doesn't pull prediction markets or podcasts.
- **This skill** — catalyst-first. User names an event; this skill
  identifies the impacted instruments, runs an investment-memo-shaped
  analysis on each, and layers in polymarket + podcast commentary. Use
  this whenever the *seed* is a catalyst, not a ticker.

## Skills this memo composes

Load only **`deep-research`** inline — enough to frame the catalyst phase
and brief the sub-agents. Everything else either runs inside a sub-agent
(which loads its own skills) or is invoked via lightweight grep/curl from
this skill:

- `deep-research` — catalyst phase (load inline)
- `financial-analysis` — financial layer per instrument; pulls in
  `yfinance`, `fred`, `sec-edgar` as needed (run inside `delegate`)
- `reddit-research` + `xai-search` — community and X/Twitter sentiment
  (run inside `delegate`)
- `polymarket` — forward-looking implied probabilities (load inline; cheap)
- `fact-checker` — never loaded inline; always invoked via `delegate` with
  a fresh context window (Step 7)
- Podcast corpus — searched directly with grep over
  `context/workspace/podcasts/**/*.md`. See Step 6.

## Delegation model

Same as `investment-memo`: `delegate` is synchronous — it buys context
isolation, not parallelism. Each sub-agent runs under a hard ~300s timeout.
Delegate bulky phases (deep-research, financial, sentiment, fact-check)
separately; never bundle them into one mega-delegate. Trivial lookups
(resolving a ticker, one polymarket search, one grep over the podcast
corpus) run inline.

## Workflow

### Step 1 — Frame the catalyst

Write down (mentally or to scratch):

- **The catalyst itself** — what is it in one sentence? A drug? A
  regulatory action? A product? A macro event? An M&A rumor?
- **The claim being made** — what is the buzz *saying* will happen? (e.g.
  "RETA could create a new TAM in obesity beyond GLP-1s")
- **Catalyst type** — drives source selection:
  | Catalyst type | Add these sources |
  |---|---|
  | Drug / biotech | SEC filings (sponsor), ClinicalTrials.gov via web, biotech podcasts |
  | Product launch | reviews/teardowns via web, reddit, X early-access chatter |
  | Regulatory / antitrust | filings, agency press releases, legal podcasts |
  | M&A rumor | press, X analyst chatter, polymarket deal-close markets |
  | Macro print | FRED data, Fed commentary, macro podcasts, polymarket |
  | Geopolitical | news, polymarket conflict markets, geopolitics podcasts |
- **Time horizon** — when does this catalyst resolve? (FDA PDUFA date, trial
  readout, election day, earnings, etc.) This frames every scenario.

This step is your internal write-down — do not yet reply to the user.
Misalignments will be caught at the Step 2.5 gate. The only reason to
break out early is if the seed is so opaque you cannot even draft an
"Understanding" line (e.g., "look at that thing I sent yesterday" with no
context) — in that case, ask one focused clarifying question and stop.

### Step 2 — Map catalyst → instruments

Before any research, list the instruments the catalyst plausibly affects:

- **Primary** — the sponsor / owner / direct beneficiary. Usually one name.
- **Competitors** — names whose value moves the *other* way if the catalyst
  resolves favorably for the primary. (RETA winning is bad for NVO,
  partially bad for VKTX, etc.)
- **Supply chain / picks-and-shovels** — second-order beneficiaries (CDMOs
  for biotech, foundries for chips, etc.). Optional; include only when
  the catalyst's TAM is large enough that second-order matters.
- **Index / sector ETF** — only if the catalyst is sector-wide.

If you don't yet know enough to map instruments confidently, that's fine —
note your best guess; the user will redirect at the Step 2.5 gate and the
catalyst phase will surface names you missed.

### Step 2.5 — Confirm catalyst understanding & plan

Before running deep-research or any other bulky phase, present a plan to
the user and **stop**. This is the most expensive part of the workflow
(catalyst deep-research + per-instrument financial fan-out + sentiment +
fact-check easily runs 10+ minutes); a 30-second confirmation gate saves
re-runs when you've misread the catalyst, the wrong instruments, or the
wrong time horizon.

**Do not call any data-fetching tool** (`deep-research`'s searches,
`financial-analysis`, `reddit-research`, `xai-search`, `polymarket`, grep
on the podcast corpus, `WebFetch`, `delegate`) in the same turn the plan
is emitted. The next turn belongs to the user.

**Plan template** — emit inline as your text reply, not as an attachment:

```markdown
**Catalyst memo plan**

**Catalyst (my understanding):** {one sentence — your restatement of what
the buzz/event is, in your own words. Be specific: name the drug / product /
event / ruling, not just the company.}

**Catalyst type:** {drug | product launch | regulatory / antitrust | M&A
rumor | macro print | geopolitical | other}

**Time horizon:** {when does this resolve — PDUFA date, trial readout
window, ruling expected, election day, earnings, etc. If unknown, say so.}

**Impacted instruments:**
- **Primary:** {ticker — why}
- **Competitors:** {ticker — why}, {ticker — why}
- **Second-order (optional):** {ticker — why} *(omit line if none worth covering)*

**Sub-questions → sources:**
1. What is the catalyst → web-search + {SEC / ClinicalTrials.gov / agency filings as relevant}
2. How big could it be (TAM / magnitude) → web-search + financial-analysis
3. Bull case → web-search + xai-search
4. Bear case → web-search + reddit-research
5. Who else is in this space → web-search + sec-edgar
6. Load-bearing numbers → financial-analysis (per instrument)

**Forward-looking signals:**
- **Polymarket:** {markets I'll search for — e.g., "FDA approval of retatrutide by 2027", "GLP-1 market share by 2027" — or "no obvious market expected, will search broadly"}
- **Podcasts:** grep keywords `{e.g., retatrutide|tirzepatide|GLP-1|\bLLY\b|\bNVO\b}` over `context/workspace/podcasts/` *(say if the corpus is known empty)*

**Deliverable:** Fact-checked PDF memo with executive summary, catalyst
section, who-wins/loses per-instrument block, TAM, bull/bear, forward-looking
probabilities, podcast commentary, sentiment, per-name scenarios, and a
verdict on the primary instrument.

Does this look right? Things worth flagging: did I get the catalyst right?
Are the impacted instruments the ones you'd want covered? Anything missing
(competitor I didn't name, podcast keyword to add, sub-question to drop)?
```

**After emitting the plan, stop.** No tool calls. Wait for the user.

**When the user replies:**

- "Looks good" / "go" / "yes" / 👍 → proceed to Step 3 with the plan as-is.
- "Add ticker X" / "drop Y" / "the catalyst is actually Z" / "narrower
  horizon" → revise the plan, re-emit the updated version, and stop again
  to re-confirm. Do not start research on a half-confirmed plan.
- A question about the plan → answer it, re-emit if anything material
  changed, and stop.

**Skip conditions** — skip this step and go straight to Step 3 only when:

1. **The user explicitly opted out** in this turn or the immediately
   preceding one — phrases like "skip the plan", "just go", "don't ask,
   just research", "quick memo", "no preview". Durable for the current
   memo only.
2. **The original request already specified all five:** the catalyst
   (precisely named), the impacted instruments (or "just the obvious
   primary"), the time horizon, the angle (bull / bear / open), and the
   deliverable. A prompt like *"Catalyst memo on retatrutide for LLY,
   NVO, VKTX, ahead of the late-2026 readouts — give me a verdict on
   LLY, skip the plan"* qualifies. The seed example from the user
   (*"RETA is a new drug LLY is testing and it's building buzz for new
   TAM"*) does **not** — it names the catalyst but not the instrument
   universe, horizon, or whether the deliverable wants a verdict.

If you skip, say so in one line ("Scope is clear — going straight to
research.") so the user knows the gate was bypassed and can interrupt if
you misjudged.

### Step 3 — Catalyst phase (deep-research)

Follow the `deep-research` workflow but tailored to the catalyst type.
Decompose into 4–6 sub-questions; for a catalyst memo these always include
some version of:

- **What is the catalyst** — mechanism, current status, key dates.
- **How big could it be** — TAM, peak revenue, addressable market, magnitude
  of the macro print, etc. Quantify wherever possible.
- **What's the bull case for it resolving favorably** — strongest evidence.
- **What's the bear case** — what would invalidate the buzz.
- **Who else is in this space** — confirms or expands the instrument map
  from Step 2.
- **What are the load-bearing numbers** — trial endpoints, peak sales
  estimates, deal probabilities, etc. These feed Step 4.

This phase is bulky — `delegate` it to a sub-agent if the search surface is
wide (drug trials, multi-jurisdiction regulatory, etc.) and have the
sub-agent return a compact digest with source URLs.

After this phase, **lock in the instrument list** from Step 2 with any
additions surfaced by the research. Every named instrument from here on
must be covered in Steps 4–7.

### Step 4 — Financial phase (per instrument)

For each instrument identified in Step 2/3, run a financial pass via
`financial-analysis`. The depth scales with how central the name is:

- **Primary instrument** — full pass: `profile`, `multiples`, `financials`,
  plus a scenario block that explicitly prices the catalyst (TAM × share ×
  margin × multiple, with both "catalyst resolves" and "catalyst fizzles"
  branches).
- **Competitors / second-order names** — slim pass: multiples + a one-line
  scenario describing the asymmetry. Don't write a full mini-memo per name
  — depth would bloat the deliverable.
- **Private / non-public** — note "no public financials" and rely on the
  research and sentiment layers for that name.

For 3+ names, `delegate` the whole financial phase to one sub-agent and ask
it to return a compact table (ticker, multiples, scenario_low,
scenario_base, scenario_high, key_risk). For 1–2 names, run inline.

### Step 5 — Forward-looking probability layer (polymarket)

This is one of the two layers that distinguishes this skill from
`investment-memo`. Load `polymarket` (cheap, no sub-agent needed) and run:

```bash
# Search for any market touching the catalyst
python skills/polymarket/polymarket.py search "<catalyst keywords>" --active-only --limit 10
```

What to look for:

- **Direct markets** — "FDA approves drug X by date Y", "Company A acquires
  Company B by date Y", "Fed cuts by N bps in [month]". When present, the
  implied probability is the single most quotable forward-looking number
  in the memo.
- **Indirect markets** — election outcomes, GDP prints, etc. that gate the
  catalyst. Include only if load-bearing.
- **No market found** — common; that's itself a finding ("no liquid
  prediction market exists for this question; the closest proxy is …").
  Do not invent markets that don't exist.

Quote market URL, both outcome prices, and the `fetched_at` timestamp. This
becomes a paragraph in the synthesis section called **Forward-looking
probabilities**, never floated without context.

### Step 6 — Key-podcast commentary layer

The other layer that distinguishes this skill. Search the local podcast
corpus for episodes that discuss the catalyst or the impacted instruments.

**Corpus location:** `context/workspace/podcasts/**/*.md`. Each transcript
is a markdown file with YAML frontmatter:

```yaml
---
podcast: "All-In"
episode_title: "Obesity drugs, AI, and the Fed"
date: "2026-04-12"
hosts: ["Chamath", "Sacks", "Friedberg", "Calacanis"]
guests: []
url: "https://..."
---

[transcript body]
```

**The corpus may be empty or sparse** — an ingestion job is being built
separately. If the directory does not exist or returns no hits, say so
explicitly in the memo ("no podcast coverage found in the local corpus as
of {date}") and move on. Do not fabricate quotes.

**Search pattern:**

```bash
# Find episodes mentioning the catalyst or any tracked ticker
grep -rli -E "retatrutide|tirzepatide|\\bLLY\\b|\\bNVO\\b" \
  context/workspace/podcasts/ 2>/dev/null | head -20

# For each hit, read the file (frontmatter + transcript) and extract the
# relevant 1–2 paragraphs around the mention
```

For each useful hit:

1. Capture: **podcast name, episode title, date, host(s) or guest who
   said it, the URL, and a short verbatim quote (1–3 sentences)**.
2. Cite as a numbered source in the `## Sources` list with type
   `[Podcast]`.
3. Group quotes into the synthesis's **Expert & podcast commentary**
   section, organized by stance (bullish on the catalyst / bearish /
   undecided).

**Treat podcast quotes as opinion, not fact** — same rule as Reddit/X. A
podcaster saying "RETA peak sales will be $40B" is not a citation for a
$40B number in the financial section; it's a citation for the *opinion that
some sophisticated observers think it could be that large*.

### Step 6.5 — Sentiment phase (reddit + X)

Always include — same as `investment-memo`. Run after the catalyst phase
so the searches are informed by what the catalyst actually is.

- `reddit-research` → 2–4 targeted searches. For a biotech catalyst try
  `r/biotechplays`, `r/investing`, `r/stocks`. For consumer catalysts add
  the relevant product subs.
- `xai-search` → `x_search` on the catalyst name, the primary ticker, and
  the catalyst date. Look for analyst chatter, insider commentary,
  meme/retail attention.

Capture: direction of sentiment, crowdedness, notable counter-takes.
Delegate this phase if the search surface is wide.

### Step 7 — Assemble the draft

Same shared header convention as `investment-memo` and
`deep-research-guided`. Write the markdown to
`context/workspace/generated/{catalyst-slug}-{YYYY-MM-DD}.md`.

```markdown
# {Long-form descriptive title — magazine-cover style, names the catalyst and the primary ticker(s)}
## {Subtitle framing the question — e.g., "Does retatrutide open a new TAM, and what does that mean for LLY?"}

**Date:** {Month DD, YYYY}

**Prepared for:** {user's name from context/identity.md — omit line if unknown}

**Prepared by:** {agent's name from context/identity.md — omit line if unknown}

**Subject:** {One- to two-sentence framing — what catalyst, what instruments, what hypothesis}

**Thesis (one line):** {The directional view — typically of the primary instrument under the catalyst's most likely resolution}

**Status:** Draft — not yet independently fact-checked

---

## Executive Summary

{3–5 short paragraphs. Lead with the headline thesis in one sentence — usually
phrased as "If the catalyst resolves X, [primary] is Y and [competitor] is Z."
Then: strongest evidence for the catalyst resolving favorably, strongest
evidence against, the prediction-market price (or absence thereof), key risks
that would flip the view, bottom-line "so what" with a verdict line on the
primary instrument. The closing line states **Buy / Sell / Avoid / Pass /
Hold** + confidence (Low / Medium / High) + the 1–2 risks-that-would-flip-it.}

## The catalyst (long form)

{2–4 paragraphs. Mechanism, current status, key dates, why it matters.}

## Who wins, who loses

{Per-instrument paragraph block. Primary first, then competitors, then any
second-order names. Each block ends with one line: the instrument's verdict
under the most-likely catalyst resolution.}
```

After the shared header and the two anchor sections above, the body has no
prescribed outline — structure it however the analysis demands — but it
must cover every one of these analytical moves:

- **TAM / magnitude** — how big is the catalyst, with numbers and sources.
- **The bull case** for the catalyst resolving favorably.
- **The bear case** — what would invalidate the buzz.
- **What the market is missing** — non-consensus angle, if any.
- **Forward-looking probabilities** — polymarket implied probabilities, or
  the explicit absence thereof.
- **Expert & podcast commentary** — grouped quotes from key podcasts, with
  numbered source citations.
- **Sentiment & positioning** — reddit + X read on crowdedness and
  direction.
- **Per-name financial coverage** — multiples + scenario for every
  instrument named in Step 2/3. None silently dropped.
- **The verdict** — per-instrument, in the Executive Summary.

**Inline citations and Sources** — exact same convention as
`investment-memo` and `deep-research-guided`: numbered markers
`[^N^](#src-N)` in the body, matching `[]{#src-N}` anchors in the
`## Sources` list. Number sources in first-appearance order. Tag each
source by type in the list: `[Web]`, `[Reddit]`, `[X]`, `[SEC]`,
`[Polymarket]`, `[Podcast]`. The `## Fact-Check Addendum` (Step 8) goes
*after* `## Sources`.

**Honesty rules** (same as `investment-memo`, non-negotiable):

- Cite every number with a numbered marker, source, and as-of date.
- Flag estimates and user-supplied assumptions.
- Acknowledge what you don't know.
- Treat Reddit/X/podcasts as **sentiment signal, not facts**.
- Quote prediction-market prices with `fetched_at` — they move
  minute-by-minute.
- If the podcast corpus returned no hits, **say so explicitly**. Never
  fabricate a quote or attribute one to a host who didn't say it.

### Step 8 — Fact-check (default, not optional)

After the markdown draft is complete and before rendering the PDF,
`delegate` to a fresh sub-agent loading `fact-checker`. Same pattern as
`investment-memo` Step 6:

```python
delegate(task="""
Fact-check the catalyst memo below. Load the `fact-checker` skill and follow
the "Sub-agent workflow" section exactly. Return the structured report as
your final response.

<<<CONTENT_TO_FACT_CHECK
[paste the full draft markdown here, OR write to disk and provide path]
CONTENT_TO_FACT_CHECK>>>
""")
```

For drafts >50KB, write to `context/workspace/scratch/memo-{slug}-{date}.md`
first and pass the path.

When the sub-agent returns:

1. Apply each ❌ Contradicted and ⚠️ Partially accurate correction inline.
2. Update the **Date** line to `{original date} (updated {date} —
   fact-checked, corrected & expanded)`.
3. Update the **Status** line to `Fact-checked {YYYY-MM-DD} — corrections
   incorporated`.
4. Append a `## Fact-Check Addendum` section after Sources.

**The fact-check verifies stated claims — not omissions or category
errors.** Universe-integrity (every instrument named in Step 2/3 is
covered, no name silently dropped, no podcast quote fabricated) is *your*
job, not the fact-checker's.

**Skip only if** the user explicitly opts out. The buzz/rumor nature of
catalyst memos makes fact-checking *more* important than for instrument
memos, not less — the load-bearing numbers (trial results, deal terms,
poll numbers) are exactly the kind of thing the rumor mill garbles.

**If `delegate` times out**, do not retry. Set Status to `Draft —
fact-check timed out; scoped follow-up recommended`, deliver as-is, and
flag in the text reply that a scoped fact-check would complete.

### Step 9 — Deliver

Convert the fact-checked markdown to PDF with plain pandoc — same path as
`investment-memo` and `deep-research-guided`:

```bash
pandoc context/workspace/generated/{slug}-{date}.md \
  -o context/workspace/generated/{slug}-{date}.pdf
```

Do **not** use HTML / headless Chromium / weasyprint — the LaTeX route
produces a typeset document; the HTML route looks like a printed webpage.

Attach:

```
attach(path="context/workspace/generated/{slug}-{date}.pdf")
```

If pandoc fails, attach the `.md` as fallback.

In the text reply, post the **Executive Summary** verbatim plus one line on
whether the fact-check found material corrections. The full memo is the
attachment.

## Example end-to-end

User: *"I heard RETA is a new drug LLY is testing and it's building buzz
among investors because of its potential to create a new TAM."*

1. **Frame** — catalyst is retatrutide (triple-agonist obesity drug);
   claim is "new TAM beyond GLP-1"; type is biotech / drug; horizon is
   FDA approval timing + Phase 3 readouts.
2. **Map instruments** — primary LLY; competitors NVO (semaglutide,
   tirzepatide rival), VKTX (early-stage triple-agonist), AMGN (MariTide);
   second-order: CDMO suppliers if relevant.
2.5. **Confirm** — emit the plan with the catalyst restatement, the
   instrument map, the polymarket/podcast search keywords, and the
   verdict-mode question. **Stop.** Wait for the user. They might reply
   "drop AMGN, add ROIV" or "narrow the horizon to the 2026 readout
   only" — revise and re-confirm before any deep-research runs.
3. **Catalyst phase** *(after confirmation)* — delegate deep-research to
   map trial status, peak sales estimates, mechanism vs. GLP-1, approval
   timeline.
4. **Financial** — full pass on LLY (scenario: RETA approved on-time vs.
   delayed vs. failed); slim pass on NVO, VKTX, AMGN multiples + asymmetry.
5. **Polymarket** — search for "obesity", "GLP-1", "FDA approval LLY",
   "weight loss". Quote any live market with implied probability and
   timestamp; note absence if none found.
6. **Podcasts** — grep corpus for `retatrutide|tirzepatide|\bLLY\b|GLP-1`.
   Pull quotes from biotech / healthcare podcasts. If corpus is empty, say
   so explicitly.
7. **Sentiment** — reddit (`r/biotechplays`, `r/investing`) + xai-search
   on `$LLY retatrutide`, `RETA obesity TAM`.
8. **Assemble** — header + Executive Summary (verdict on LLY) + The
   catalyst + Who wins/loses + TAM + bull/bear + forward-looking
   probabilities + podcast commentary + sentiment + per-name financials.
9. **Fact-check** — delegate.
10. **Deliver** — PDF via pandoc, attach, post Executive Summary in reply.

## Common mistakes

- **Treating the seed as a ticker when it's a catalyst.** If the user
  named an event, mapping to instruments is *your* job (Step 2). Do not
  ask "which ticker do you want analyzed" when the answer is obvious from
  the catalyst.
- **Skipping the Step 2.5 confirmation gate on a buzz seed.** Buzz items
  are *exactly* the case where misreading the catalyst is expensive: the
  rumor mill garbles drug names, deal terms, and dates. The default is
  always to confirm; skip only when the user explicitly opted out or the
  seed already specified catalyst + instruments + horizon + angle +
  deliverable.
- **Starting deep-research in the same turn the plan was presented.**
  The plan turn must end with the user's confirmation question. No
  `deep-research` calls, no `delegate`, no `WebFetch`, no `polymarket`,
  no podcast grep, until the user replies.
- **Treating the plan as one-way communication.** If the user pushes
  back ("add this ticker", "wrong catalyst", "narrower horizon"), revise
  the plan and re-emit. One more round of clarification is cheaper than
  10 minutes of researching the wrong thing.
- **Silently dropping a competitor or second-order name from Step 2/3.**
  Every name listed must be covered, even if thinly. If a name turns out
  not to matter, say so in one line — don't omit it.
- **Fabricating a podcast quote.** If the local corpus has no hits, say
  "no podcast coverage found in the local corpus as of {date}" and move
  on. Never invent a quote or attribute one to a host who didn't say it.
- **Inventing a polymarket market.** If no market exists for the
  catalyst, that's a finding — say so. Do not paraphrase an opinion as if
  it were a market price.
- **Quoting a polymarket price without `fetched_at`.** Prediction-market
  prices move minute-by-minute; the timestamp is what makes the number
  citable.
- **Treating podcast quotes as financial facts.** A podcaster's peak-sales
  number is opinion. Cross-reference factual claims against authoritative
  sources before they enter the financial section.
- **Skipping prediction markets / podcasts because "they're optional".**
  They are not optional in this skill — they are the two layers that
  distinguish it from `investment-memo`. Empty results count as
  coverage; silent skips don't.
- **Skipping the fact-check.** Catalyst memos are built on buzz; the
  rumor mill is exactly where load-bearing numbers get garbled. Always
  delegate to `fact-checker`.
- **One mega-delegate for the whole memo.** Research + financials +
  sentiment + fact-check in one `delegate` exceeds the 300s budget and
  loses everything on timeout. Delegate bulky phases separately.
- **Rendering the PDF via HTML/Chromium/CSS.** Use plain pandoc (LaTeX).
- **Bare URLs instead of numbered markers.** Every claim cites sources
  with `[^N^](#src-N)`. Marker/anchor mismatch renders as a dead link.
- **Attaching .md instead of .pdf.** Always render PDF first; .md is
  only a fallback when pandoc fails.
- **Forgetting `attach()`.** The PDF must be attached, not just written
  to disk.
- **Header lines without blank lines between them.** Each `**Field:**`
  line in the header needs a blank line before the next, or pandoc
  renders them as a run-on paragraph.
