---
name: gtm-position-segment
description: "Use when a product context document is finalized and ready for ICP identification, messaging, and pricing positioning. Trigger: builder has a completed product context from onboard-ingest and wants to define who to sell to, what to say, and where to price."
---

# Position & Segment

Takes a finalized product context (from `gtm-onboard-ingest`) and produces prioritized ICP cards with messaging and pricing signals. Two-pass approach: lightweight ICP candidates first, builder selects, then deep per-ICP research before generating full output.

**Requires:** A finalized product context file with readiness assessment checkboxes checked. Web tools optional — enhances per-ICP research but skill works as pure synthesis without them. When available: `web-search` skill (Brave), `reddit-research` skill (Reddit access), `linkedin-research` skill (LinkedIn via search indexes), `gemini-search` skill (Google grounded search), `xai-search` skill (X/Twitter).

## Tool Priority

Same stack as onboard-ingest, all optional. Check availability and use the best available:

| Capability | Best | Fallback | Last resort |
|-----------|------|----------|-------------|
| Page content | `playwright` skill (headless browser) | `web_fetch` (direct) | — |
| Web research | `gemini-search` skill (curl to Gemini API) | `web-search` skill (Brave API) | Skip new research |
| Social listening (X/Twitter) | `xai-search` skill (curl to xAI API) | — | Skip social layer |
| Reddit / forums | `reddit-research` skill (Brave + JSON API) | `xai-search` with `allowed_domains: ["reddit.com"]` | Skip Reddit layer |
| LinkedIn | `linkedin-research` skill (search index) | `gemini-search` with LinkedIn query | Skip LinkedIn layer |

At the start of each run, probe for available tools (MCP servers, `XAI_API_KEY` for xai-search skill, `GEMINI_API_KEY` for gemini-search skill). If none are configured, the skill works as pure synthesis from the product context — ICP cards and messaging are generated from existing data only, with a warning that output would be stronger with web research enabled.

## Workflow

### Step 0: Intake

Collect from the builder (ask only what's not already provided):

1. **Product context path** (required) — path to the finalized product context file
2. **Output path** — where to write the positioning file (default: `./positioning.md`)
3. **Competitive landscape path** (optional) — path to `competitive-landscape.md` if it exists. If not provided, check for `competitive-landscape.md` in the same directory as the product context. If found, read it for richer competitive data. If not found, proceed with competitive data from the product context only.

Read the product context file. Check the **Readiness Assessment** section at the bottom:

- If all checkboxes are checked → proceed
- If any are unchecked → stop. Tell the builder: "Your product context isn't finalized yet. These items are incomplete: [list]. Run onboard-ingest to completion before starting positioning."

### Step 1: Generate ICP Candidates

Synthesize from the product context. Read these sections closely:

- **Target Buyer** — the hypothesis from ingestion is your starting point, not your answer
- **Competitive Landscape** — who competitors target reveals adjacent ICPs
- **Buyer Language** — how different personas describe the problem
- **Gaps & Opportunities** — underserved needs often map to underserved personas
- **Competitive Landscape doc** (if available) — `competitive-landscape.md` provides deeper competitive data: per-competitor community sentiment, incumbent threat assessments, and moat analysis. Use this to identify ICPs that competitors are targeting poorly or ignoring.

Produce **2-3 ICP candidates**. For each:

| Field | What to write |
|-------|--------------|
| **Label** | Short name (e.g., "Mid-market Engineering Manager") |
| **Who** | Role, company size, industry |
| **Why they'd buy** | Pain point + buying trigger |
| **Confidence** | High / Medium / Low — based on evidence strength in the product context |
| **Evidence** | Specific references from the product context supporting this ICP |
| **Key risk** | The assumption most likely to be wrong — what needs validation |

**Rank by confidence.** Lead with your recommendation: "We think you should start with ICP #1 because..."

### Step 2: Builder Selects ICPs

Present the candidates as assertions. For each: "We think [label] is a strong ICP because [evidence]. The main risk is [risk]."

The builder's job:
- **Keep** — proceed to deep research
- **Drop** — remove from consideration
- **Reorder** — change priority
- **Add** — suggest an ICP the system missed

If the builder adds a new ICP, accept it but flag: "We don't have evidence for this ICP yet. We'll research it in the next step, but confidence will depend on what we find."

Proceed only with selected ICPs.

### Step 3: Deep Research Per Selected ICP

For each selected ICP, two research passes. **Skip this step entirely if no web tools are available** — go straight to Step 4 using product context data only, and note this limitation in the output.

**Pass A — Back-reference:** Follow source URLs from the product context's **Ingestion Sources** tables. Re-read the original material, but through the lens of the specific persona:
- In community threads: filter for posts by people matching the ICP profile
- In competitor pages: how does the competitor target this persona specifically?
- In reviews: which complaints come from this persona vs. others?

**Pass B — New research:** Use `references/icp-research.md` for the search playbook. Targeted searches:
- Job postings for the ICP role — reveals their priorities, tools, and language
- Community spaces where this persona hangs out
- How this persona describes the problem (may differ from general buyer language)
- Pricing expectations for this persona's company size/segment

**Pass B+ — Competitive landscape integration:** If `competitive-landscape.md` exists, read it for ICP-specific competitive insights instead of doing fresh competitor research:
- Which competitors specifically target this persona? (from per-competitor deep dives)
- What do users in this persona's segment say about competitors? (from community sentiment)
- Which incumbent moves affect this persona most? (from incumbent adjacents)
- What moat advantages matter for this persona? (from moat analysis)
This replaces the "Competitor content specifically targeting this persona" search from the original Pass B — the competitive-landscape skill has already done this work in depth.

**Pass C — Channel blind-spot check:** Read `docs/channel-playbook.md` (shared reference). Scan the full channel list and ask: "Given this ICP's role, company size, and behavior — which channels would they respond to that haven't surfaced in Passes A and B?" Add any plausible channels to the ICP card's Channels field.

**Parallelize across ICPs** using subagents where possible. Each ICP's research is independent.

Write intermediate findings to a scratchpad so the builder can see progress.

### Step 4: Build Full ICP Cards + Messaging

For each selected ICP, generate the full output using `templates/icp-card.md`. Read it now.

**ICP Card:**
- Role/title, company size, industry vertical
- Pain points (specific, grounded in research — not generic)
- Buying triggers (what event causes them to search)
- Common objections (what makes them hesitate)
- Current solution (what they use today, or manual process)
- Channels to reach them (where they hang out, what they read)
- Example companies (3-5 real companies matching the profile)

**Strategic Messaging:**
- Positioning statement for this ICP (one sentence: "For [who] who [pain], [product] is [category] that [differentiator]")
- 2-3 key angles (each angle is a message direction with reasoning)
- Tone guidance (formal/casual, technical/accessible — based on how this persona communicates)
- Proof points (what evidence supports the claim — metrics, case studies, technical details)

**Tactical Messaging:**
- 3-5 draft headlines (for landing pages, ads)
- 2-3 email subject lines (for outbound)
- Elevator pitch (30 seconds, spoken)
- One-liner (one sentence, written)
- All grounded in buyer language from research — use their words, not the builder's

**Pricing Signals:**
- What this persona expects to pay (based on segment norms)
- Market reference points (competitor pricing for this segment)
- Positioning recommendation: budget / market-rate / premium — with reasoning
- Model fit (per-seat, usage-based, flat — based on how this persona buys)

**Every claim must have a source** — URL, product context section reference, or builder statement.

### Step 5: Builder Validates

Present one ICP at a time. For each:

1. Show the full ICP card — "Here's who we think this buyer is"
2. Show strategic messaging — "Here's how we'd position for them"
3. Show tactical messaging — "Here's what that sounds like in practice"
4. Show pricing signals — "Here's where we'd price for this segment"

After each ICP, ask: "What's off? What resonates? What's missing?"

**Convergence:** Update based on builder feedback, re-present changed sections. Should converge in 1-2 rounds per ICP. If a builder fundamentally rejects messaging direction, the issue is likely in the ICP definition, not the copy — go back to the card.

### Step 6: Finalize

Assemble the complete positioning document using `templates/positioning.md`. Read it now.

**Exit gate check — ask the builder directly:**

> "Looking at this positioning document: could a GTM plan be built from it? Specifically:"
> 1. "Do you know who to target first and where to find them?"
> 2. "Could you draft a real outbound email from the messaging here?"
> 3. "Do you know where you're priced and why?"
>
> "If the answer to all three is yes, we're done. If not, what's missing?"

Write the final file to the output path.

## Tips

- The product context's **Target Buyer** section is a starting hypothesis, not a conclusion. Don't just copy it into ICP #1. Challenge it with the competitive landscape and buyer language data.
- Confidence scores matter. A "Low confidence" ICP with high potential is still worth presenting — just be honest that it's speculative. The builder may have context that confirms it.
- Tactical messaging is meant to provoke reactions. The builder saying "I'd never say it that way" is useful — it reveals tone and positioning preferences that were implicit.
- When builder feedback contradicts research, don't just defer. Say "the research suggests X but you're saying Y — here's why that matters for messaging" and let the builder decide.
- Pricing signals are positioning tools, not pricing strategy. "You're the affordable alternative" is a positioning choice. "Charge $12/seat/month" is a pricing decision — don't make it.

## Common Mistakes

- **Generating messaging from summaries instead of raw signal** — the product context is a synthesis. Messaging needs specific buyer quotes, competitor weaknesses, and persona-specific pain. That's why Step 3 goes back to source URLs.
- **All ICPs sound the same** — if your ICP cards could be swapped between any B2B SaaS product, they're too generic. Ground every field in specifics from the product context and research.
- **Skipping the builder selection step** — generating full messaging for all candidates before the builder weighs in wastes effort and buries the important decision (who to target) under a wall of content.
- **Treating builder objections as copy edits** — when a builder rejects tactical messaging, the problem is usually upstream (wrong angle, wrong pain point, wrong ICP assumption). Don't just rewrite the headline — check the strategic layer.
- **Presenting pricing as recommendations** — the skill produces pricing *signals* (what the market expects, where competitors price, what the persona's segment suggests). The builder decides what to charge.
- **Not attributing sources** — every claim needs a source. "Mid-market buyers expect a free tier" needs to point to the competitor pricing data or community thread that supports it. Unattributed claims get ignored or challenged.
