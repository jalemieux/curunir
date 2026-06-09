---
name: gtm-competitive-landscape
description: "Use when a product needs comprehensive competitive research — deep discovery across direct competitors, buyer-segment competitors, and incumbent adjacents, with community sentiment, pricing intelligence, and moat analysis. Trigger: builder has a product context (from onboard-ingest) or can provide product name + buyer + differentiators, and wants to understand the full competitive landscape."
---

# Competitive Landscape

Comprehensive competitive research that goes far deeper than a quick competitor scan. Discovers direct competitors, buyer-segment competitors, and incumbent adjacents, then researches each in depth: positioning, pricing, community sentiment, review platforms, content/SEO. Includes an incumbent feature watch that catches recent moves by large products in the buyer's stack — the kind of move that kills indie products when missed.

Goal: produce a competitive landscape document thorough enough that a founder knows exactly who they're competing with, where their moat is strong, where it's vulnerable, and what to watch.

**Requires:** `web_fetch` tool at minimum. `gemini-search` skill for grounded web search. `xai-search` skill for X/Twitter social listening. `web-search` skill for Brave Search. `reddit-research` skill for Reddit access. `linkedin-research` skill for LinkedIn content via search indexes. API keys (`BRAVE_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`) come from the container environment / `.env`.

## Tool Priority

Same stack as the rest of LaunchKit. Check availability in order and use the best available:

| Capability | Best | Fallback | Last resort |
|-----------|------|----------|-------------|
| Page content | `playwright` skill (headless browser) | `web_fetch` (direct) | — |
| Web research | `gemini-search` skill (curl to Gemini API) | `web-search` skill (Brave API) | — |
| Social listening (X/Twitter) | `xai-search` skill (curl to xAI API) | — | — |
| Reddit / forums | `reddit-research` skill (Brave + JSON API) | `xai-search` with `allowed_domains: ["reddit.com"]` | — |
| LinkedIn | `linkedin-research` skill (search index) | `gemini-search` with LinkedIn query | — |

## Workflow

### Step 0: Tool Check & Input Validation

**This step is mandatory. Do not skip it.**

Before anything else, probe for all tools listed in the Tool Priority table:

1. **Playwright** — check if the `playwright` Python package is available (`python -c "from playwright.sync_api import sync_playwright"`)
2. **xAI API** — verify `XAI_API_KEY` is set
3. **Gemini Search** — verify `GEMINI_API_KEY` is set
4. **Brave Search** — verify `BRAVE_API_KEY` is set

Log which are available and which are missing. Record the results for the output document's Tool Coverage section.

If critical tools are missing, warn the builder: "Social listening / grounded search is unavailable — coverage in those areas will be limited."

**Input validation:**

Check if a product context path was provided:

- **If product context exists:** Read it. Extract: product summary, buyer hypothesis, key differentiators, any competitors already mentioned, and the buyer's "current solution" list (these become incumbent adjacents).
- **If no product context exists:** Ask the builder for minimum required inputs:
  1. Product name
  2. What it does (one sentence)
  3. Who the buyer is
  4. Key differentiators (what makes it different)
  5. What tools the buyer currently uses (these become incumbent adjacents)

If product context exists but is missing required fields (no buyer hypothesis, no differentiators), ask the builder to fill the gaps before proceeding.

### Phase 1: Competitor Discovery

Cast a wide net to build the full competitor list. Read `references/search-strategies.md` for the full query playbook. Three search tracks, run in parallel where possible:

**Track A — Direct competitors:**
- Category searches: "best {category} tools", "{category} comparison", "{product} alternatives", "{product} vs"
- Problem-keyword searches: "{pain point} solution", "{job-to-be-done} tool"
- Builder-mentioned competitors (from product context)
- Review platform discovery: search G2, Capterra, Product Hunt for the category

**Track B — Buyer-segment competitors:**
- Tools targeting the confirmed buyer segment, not just the product category
- Queries shaped by buyer hypothesis: "{buyer role} tools for {problem}", "best {category} for {industry}"
- These may surface competitors that look different but compete for the same budget and attention

**Track C — Incumbent adjacents:**
- From product context's "Current solution" field, identify 3-5 large products the buyer already uses
- These aren't direct competitors (yet) but have the distribution and resources to ship overlapping features
- If the builder didn't list current solutions, search for: "what do {buyer role}s use for {job-to-be-done}"

**Output of Phase 1:** A deduplicated list of entities to research, tagged as `direct`, `segment`, or `incumbent`. Present this list to the builder before proceeding: "We found these competitors and incumbents. Anyone missing?"

### Phase 2: Deep Research Per Competitor

For each direct and segment competitor found in Phase 1, research across six dimensions. Use `references/search-strategies.md` § Phase 2 for query patterns.

| Dimension | What to extract | Sources |
|-----------|----------------|---------|
| **Positioning & messaging** | Tagline, hero copy, how they describe themselves, who they say they're for | Landing page (playwright or web_fetch) |
| **Pricing** | Full model — tiers, free tier, BYOK, credits, annual discount, "contact sales" threshold | Pricing page |
| **Feature comparison** | What overlaps with the product, what doesn't, unique features | Landing page, docs, feature pages |
| **Community sentiment** | What users love, what they complain about, "switched from/to" mentions | reddit-research, xai-search (X/Twitter) |
| **Review platforms** | Ratings, review count, complaint patterns, praise patterns | G2, Capterra, Product Hunt, Trustpilot |
| **Content & SEO** | Blog presence, content velocity, which buyer-intent keywords they rank for | gemini-search or web-search |

**Source URLs are mandatory for every data point.** No unsourced claims. If you have a finding but lost the URL, search for it again before including it.

**Parallelize** research across competitors using subagents where possible. Each competitor's research is independent.

### Phase 3: Incumbent Feature Watch

**This is the phase that prevents the "Perplexity Model Council" miss.** For each incumbent adjacent identified in Phase 1:

1. **Blog/changelog scan** — search for "{incumbent} new features {year}", "{incumbent} changelog", "{incumbent} blog" — scoped to the last 90 days. Check each of the last 3 months individually.
2. **Social announcements** — xai-search for official account posts about new features; gemini-search for LinkedIn announcements
3. **Community reactions** — reddit-research and xai-search for user threads about recent incumbent launches
4. **Feature overlap assessment** — for each new feature found, assess: does this overlap with the product's core differentiators? Rate as `no overlap`, `partial overlap`, or `direct overlap`. If partial or direct overlap, note how the incumbent's implementation compares (pricing tier, depth, accessibility).

**Do not skip this phase.** The biggest competitive threats to indie products come from incumbents shipping overlapping features, not from other indie tools.

### Phase 4: Market Signals

Broader landscape intelligence. Use `references/search-strategies.md` § Phase 4 for query patterns.

| Signal | What to look for | Sources |
|--------|-----------------|---------|
| **Funding & hiring** | Recent raises by competitors, job postings suggesting expansion into the space | gemini-search, linkedin-research |
| **SEO positioning** | Who ranks for buyer-intent keywords, content production velocity | web-search, gemini-search |
| **Market structure** | Fragmented vs. consolidating? Dominant player emerging? Category being absorbed by incumbents? | Synthesis of all findings |

### Phase 5: Synthesis & Presentation

Present findings to the builder **in the conversation** before writing the document. Do NOT write the output document yet — that happens in Step 7 after the builder has validated.

Present these sections:

1. **Competitor comparison table** — all direct and segment competitors with positioning, pricing, feature overlap, strengths, weaknesses
2. **Per-competitor deep dives** — detailed findings per competitor
3. **Incumbent threat assessment** — what each incumbent has shipped recently, overlap risk rating
4. **Moat analysis** — which of the product's differentiators are structural (hard to replicate) vs. shippable (an incumbent could build it in a sprint)
5. **Watchlist recommendation** — which entities to track in competitive-monitor, with rationale and suggested check frequency

Each section is a claim. Tell the builder: "Here's what we found. Where are we wrong? Anyone missing?"

### Step 6: Builder Conversation

The builder reacts — corrects, adds context, fills gaps.

- Accept competitor additions and removals
- Adjust moat analysis based on builder's technical knowledge ("that feature is easy/hard to replicate because...")
- Update incumbent threat assessments based on builder's industry knowledge
- Refine watchlist based on what the builder cares about most

Should converge in 1-2 rounds. If the builder fundamentally disagrees with the competitive framing, the issue may be in the product context's buyer hypothesis — recommend revisiting.

### Step 7: Finalize

Write the complete competitive landscape document to the output path using `templates/competitive-landscape.md`. This is a single, clean write — not incremental edits.

**Exit criteria:** The builder can answer:
- "Who are my direct competitors and how do I differ from each?"
- "Which incumbents could threaten me and what have they shipped recently?"
- "Where is my moat strong and where is it vulnerable?"

## Tips

- **Incumbent feature watch is the highest-value phase.** Indie competitors are visible and slow-moving. Incumbents shipping overlapping features overnight is the existential risk. Prioritize Phase 3.
- Use subagents to parallelize Phase 2 research across competitors — each competitor's research is independent.
- The buyer's "current solution" field in product context is the most important input for identifying incumbent adjacents. If it's missing or vague, ask the builder directly.
- Community sentiment often reveals competitive dynamics that landing pages don't — "I switched from X to Y because..." is gold.
- The moat analysis is the most opinionated section. Present it as a hypothesis, not a conclusion. The builder knows their technical landscape better than the research can show.

## Common Mistakes

- **Skipping incumbent feature watch** — this is the phase that catches the Perplexity-style miss. It's tempting to skip when there are many direct competitors to research. Don't.
- **Categorizing incumbents as "adjacent" without checking recent launches** — "Perplexity is a search tool, not a competitor" was true until Feb 5, 2026 when they shipped Model Council. Always check what incumbents have shipped recently.
- **Only searching for competitors in the product's category** — buyer-segment competitors may look completely different but compete for the same budget and attention. A research tool and a consulting subscription compete for the same analyst budget.
- **Trusting landing pages over community sentiment** — competitors' landing pages show aspirational positioning. Reddit and G2 reviews show reality. Weight community sentiment higher for strengths/weaknesses.
- **Not logging research sources** — the Research Sources table is mandatory. It enables the competitive-monitor to know what was checked and when.
- **Writing the document before builder validation** — present findings in conversation first. Write once after convergence.
- **Treating moat analysis as permanent** — a differentiator that's structural today may become shippable tomorrow. The watchlist exists to catch this.

## Known Fetch Issues

| Platform | Issue | Workaround |
|----------|-------|------------|
| Reddit | Blocks web_fetch AND headless browsers (403) | Use `reddit-research` skill |
| Product Hunt | Frequently returns 403 | Ask builder for direct URL after 2 failed attempts |
| LinkedIn | Login-walled | Use `linkedin-research` skill |
| Twitter/X | Requires authentication | Use `xai-search` skill. Requires `XAI_API_KEY`. |
| G2/Capterra | Sometimes block automated access | Search for cached content via gemini-search or web-search |
| Incumbent blogs | May block or require JS rendering | Try playwright first, then web_fetch, then search for cached versions |
