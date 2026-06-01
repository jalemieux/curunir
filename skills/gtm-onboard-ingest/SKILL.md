---
name: gtm-onboard-ingest
description: "Use when onboarding a new product into LaunchKit — ingests builder-published materials, researches market signals, conducts builder validation conversation, and outputs a structured product context document. Trigger: user provides a product name/URL and wants to build deep product understanding for GTM work."
---

# Onboard & Ingest

Builds deep product understanding by consuming every available signal — published materials, market intelligence, and builder input — then synthesizes a product context that drives all downstream GTM work.

Goal: understand the product at the level a great fractional CMO would after a deep-dive week with the founder — in minutes.

**Requires:** `web_fetch` tool at minimum. `gemini-search` skill for grounded web search and YouTube summarization. `xai-search` skill for X/Twitter social listening. `web-search` skill for Brave Search. `reddit-research` skill for Reddit access (Brave discovery + JSON API extraction). `linkedin-research` skill for LinkedIn content via search indexes. API keys (`BRAVE_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`) come from the container environment / `.env`.

## Tool Priority

The skill uses multiple tools. Check availability in order and use the best available:

| Capability | Best | Fallback | Last resort |
|-----------|------|----------|-------------|
| Page content | `playwright` skill (headless browser) | `web_fetch` (direct) | — |
| Web research | `gemini-search` skill (curl to Gemini API) | `web-search` skill (Brave API) | — |
| Social listening (X/Twitter) | `xai-search` skill (curl to xAI API) | — | — |
| Reddit / forums | `reddit-research` skill (Brave + JSON API) | `xai-search` with `allowed_domains: ["reddit.com"]` | — |
| LinkedIn | `linkedin-research` skill (search index) | `gemini-search` with LinkedIn query | — |
| YouTube analysis | `gemini-search` skill (curl to Gemini API) | `web_fetch` + transcript sites | Skip video layer |
| File reading | `fs_tools` | — | — |

## Workflow

### Step 0a: Tool Check

**This step is mandatory. Do not skip it.**

Before anything else, probe for all tools listed in the Tool Priority table:

1. **Playwright** — check if the `playwright` Python package is available (`python -c "from playwright.sync_api import sync_playwright"`) for JS-rendered page fetching (SPAs, dashboards). Invoke the `playwright` skill for usage patterns. Note: Playwright is NOT needed for Reddit — use `reddit-research` skill instead. Playwright is still useful for JS-rendered pages (SPAs, dashboards).
2. **xAI API** — verify `XAI_API_KEY` is set. If available, invoke the `xai-search` skill for X/Twitter social listening (uses curl to `https://api.x.ai/v1/responses` with `x_search` and `web_search` tools).
3. **Gemini Search** — verify `GEMINI_API_KEY` is set. If available, invoke the `gemini-search` skill for grounded web search and YouTube summarization (uses curl to Gemini API with `google_search` tool).
4. **Brave Search** — verify `BRAVE_API_KEY` is set. If available, invoke `web-search` skill for general search and `reddit-research` skill for Reddit access.

Log which are available and which are missing. This determines your research coverage for the entire run.

Record the results — they will be included in the output document's Tool Coverage section.

If critical tools are missing (especially xAI API / `XAI_API_KEY`, Gemini API / `GEMINI_API_KEY`), warn the builder: "Social listening / grounded search is unavailable — coverage in those areas will be limited."

### Step 0b: Intake

If the builder provided at least a **product name and one URL**, proceed directly to Step 1. Don't ask for information the system can discover on its own.

Only ask for items that Layer 1 cannot discover and that are needed before research begins:

1. **Product name** (required — infer from URL if not stated)
2. **Output path** — where to write the product context file (default: `./product-context.md`)

The following items are **deferred to Step 4** (Builder Conversation) if not already provided. The system will attempt to discover them during Layers 1 and 2:

- Additional URLs (docs, blog, social profiles, GitHub, app store listings, video URLs)
- Uploads (pitch decks, PDFs, docs, screenshots)
- Social handles (Twitter/X, LinkedIn, Bluesky)
- Known competitors

Don't ask the builder to describe their product — the whole point is to figure that out from signals first.

### Step 1: Layer 1 — Builder-Published Materials

Crawl everything the builder provided and extract signal. Use `delegate` to process URLs in parallel where possible.

**Disambiguation check:** After the first search for the product name, check whether the name is shared with other products. If it is, note the collision and use the builder's URL (not the product name) as the primary search anchor for the rest of the run. Flag the namespace issue to the builder in Step 3.

**For each URL:**
- Use `playwright` skill or `web_fetch` to get page content
- For video URLs: use `gemini-search` skill to summarize (pass the YouTube URL in the query)
- For GitHub repos: fetch README and repo description
- For social profiles: fetch recent posts about the product

**For each upload:**
- Read with `fs_tools` (PDFs, markdown, text, slides)
- Extract key claims, positioning, feature descriptions

**Extract across all Layer 1 sources:**
- Claimed value prop and positioning
- Who the builder thinks the buyer is
- Messaging voice and tone
- Feature set as described to users
- What the builder highlights (reveals what they think matters)
- Gaps and inconsistencies across sources (reveals positioning uncertainty)
- Brand identity — color palette (primary, secondary, accent with hex values), typography, logo style, visual tone (minimal/bold/playful/corporate/etc.). Extract from landing pages, pitch decks, social profiles. This feeds downstream asset generation (landing pages, email templates, content).

### Step 1.5: Buyer Hypothesis Check

Before investing in Layer 2 research, present your buyer hypothesis to the builder for a quick validation. This is one assertion and one question — not an interview.

Format: "Based on [landing page / pitch deck / etc.], we think your buyer is **{buyer description}**. Right or wrong?"

Wait for the builder's response. Their correction (if any) determines the direction of all Layer 2 research — competitive landscape, community search queries, buyer language sources, and pricing intelligence.

**This step exists because Layer 2 research is expensive and directional.** Researching the wrong buyer means redoing the entire market analysis. A 30-second check here saves 10+ minutes of misdirected research.

### Step 2: Layer 2 — Market Signals

Use Layer 1 findings and the confirmed buyer hypothesis to fan out into market research. Read `references/search-strategies.md` for the full query playbook, including buyer-segment-specific queries.

**Phase 2a: Competitive landscape — delegated to `competitive-landscape` skill**

Invoke the `competitive-landscape` skill with the path to the product context file written in Step 1. The competitive-landscape skill handles all competitive research: direct competitors, buyer-segment competitors, incumbent feature watch, community sentiment, pricing intelligence, and moat analysis. It writes a standalone `competitive-landscape.md` alongside the product context.

Wait for competitive-landscape to complete before continuing with Phases 2b–2d.

Load the `gtm-competitive-landscape` skill (via `load_skill`) for the full workflow.

**Phase 2b: Community & buyer voice**

1. Search Reddit, HN, forums for the problem/category — find recommendation threads, complaints, wish lists. **Use `reddit-research` skill for Reddit** — it discovers posts via Brave Search and extracts full content via Reddit's JSON API. Playwright and web_fetch are both blocked by Reddit.
2. **Use the `xai-search` skill for Twitter conversations** about the product and competitors. Invoke it with `x_search` tool type for X/Twitter data. Do not skip this — it's a required capability when `XAI_API_KEY` is set.
3. Search for "best [category] tools", "[category] comparison", "alternatives to [competitor]"
4. Collect actual buyer quotes and language — these are gold for messaging
5. Search for buyer-segment-specific community language (see `references/search-strategies.md` § Buyer-Segment-Specific Queries)

**Phase 2c: Pricing intelligence**

1. Visit every identified competitor's pricing page
2. Note: model (per seat, usage, flat), tiers, free tier presence
3. Infer target buyer from pricing ($9/mo = SMB, "contact sales" = enterprise)
4. Identify market norms — free tier expected? Annual contracts standard?

**Phase 2d: SEO & demand signals**

1. Search buyer-intent keywords — who ranks? What content exists?
2. Note "People Also Ask" / related searches — reveals how buyers frame the problem
3. Identify content gaps — questions asked that nobody answers well

**Fetch failure escalation:** When a fetch fails (403, redirect loop, JS-rendered), retry once with a different tool (e.g., `playwright` skill if `web_fetch` failed). If it still fails after 2 attempts, **ask the builder for the direct URL or content** — don't burn 5+ searches trying to find something the builder can just give you. Note all failed fetches for the coverage checklist.

**Extract across all Layer 2 sources:**
- Competitive landscape map
- Gaps — problems buyers mention that nobody solves well
- Buyer language (their words, not the builder's words)
- Pricing expectations and norms
- Distribution channels where buyers gather
- Content opportunities
- Competitor weaknesses from reviews/community

### Step 2.5: Coverage Checklist

Before moving to synthesis, verify research coverage. Check each box:

- [ ] **X/Twitter social listening** — did you search X via `xai-search` skill or `site:x.com`?
- [ ] **Reddit / community forums** — did you access Reddit content (directly or via aggregators)?
- [ ] **Competitive landscape** — did the `competitive-landscape` skill complete and produce `competitive-landscape.md`?
- [ ] **SEO / search landscape** — did you check who ranks for buyer-intent keywords?
- [ ] **Buyer-segment-specific sources** — did you search communities where the confirmed buyer segment gathers?

For any unchecked items, either go back and do them, or flag the gap explicitly to the builder in Step 3: "We could not access [X/Reddit/etc.] — [social listening/community voice] coverage is incomplete."

Silent omissions are not acceptable. Every gap must be visible.

### Step 3: Synthesize & Present

Combine Layer 1 and Layer 2 findings and present them **in the conversation** to the builder. Do NOT write the output document yet — that happens in Step 5 after the builder has validated and corrected the findings.

Use the template at `templates/product-context.md` as a structural guide. Read it now.

**Critical: make assertions, not questions.** "We think your product does X for Y" provokes sharper reactions than "tell us about your product."

Present the following sections to the builder:

1. **Product summary** — one paragraph: what it does, the problem it solves, how it works
2. **Target buyer hypothesis** — who the buyer is (already validated in Step 1.5, but now with Layer 2 evidence)
3. **Competitive landscape** — summary from `competitive-landscape.md` (full analysis lives there; present key findings and incumbent threats here)
4. **Market positioning** — where the product sits vs. competitors, what makes it different
5. **Buyer language** — actual quotes from community threads and reviews, with source URLs
6. **Gaps and opportunities** — where buyers are underserved
7. **Coverage gaps** — what you couldn't access and why (from Step 2.5)

Each section is a claim. Tell the builder: "Here's what we found. Where are we wrong?"

**Source URLs are mandatory.** Every quote, claim, and data point must include a clickable URL. Source names alone ("Quora", "Reddit") are not sufficient. If you have the finding but lost the URL, search for it again before presenting.

### Step 4: Builder Conversation

The builder reacts — corrects, adds context, fills gaps. This is NOT a blank-slate interview.

**Only ask things the system genuinely couldn't find from Layers 1 and 2:**
- "We couldn't determine your pricing rationale — why did you choose X?"
- "We found no mentions of your product in community discussions — have you launched publicly yet?"
- "Your landing page says Y but a blog post from 3 months ago says Z — which reflects current direction?"
- "Who's actually paying you today, and how did they find you?"
- "What have you tried for GTM so far, and what happened?"
- "Where is this product going that isn't reflected in what's public yet?"
- Deals won and lost — why, and by whom
- Failed experiments and abandoned directions

**Also ask about deferred intake items** if they weren't discovered in Layers 1/2:
- Social profiles that weren't found
- Uploads or documents that could fill gaps
- Known competitors the system missed

**Never ask the builder to repeat what's already on their landing page.**

### Step 5: Revise & Converge

Incorporate builder corrections. Present the revised findings in the conversation. Repeat until the builder confirms accuracy.

Should converge in 2-3 rounds. If it takes more, the system missed something fundamental in Layers 1/2 — go back and research deeper.

### Step 6: Finalize

**Now** write the complete product context document to the output path, incorporating all builder corrections. Use the template at `templates/product-context.md`. This is a single, clean write — not incremental edits.

The document is complete when the builder confirms:

- **Product understanding:** "Your product does X for Y because Z" — builder says yes
- **Competitive landscape:** "Your main competitors are A, B, C, and here's how you differ" — builder agrees
- **Buyer language:** "Buyers talk about this problem like..." — builder recognizes it
- **Market gaps:** "Here's where no one serves buyers well..." — builder sees the opportunity

The exit criteria is NOT "we ingested everything." It's **"we understand enough to move to Position & Segment with confidence."**

## Tips

- Use `delegate` to process Layer 1 URLs in parallel — don't wait for one to finish before starting the next.
- Inconsistencies across the builder's own materials are valuable signal — flag them, don't resolve them silently.
- Buyer language from community threads is often more valuable than anything on the builder's own site.
- For video content, a transcript summary is sufficient — don't try to analyze visuals.
- **Fetch failure escalation:** If you can't find something the builder claims exists (a launch, a listing, a review), ask the builder for the direct URL after 2 failed attempts. Don't burn 5+ searches. The builder knows where their stuff lives.
- **Reddit blocks automated fetches AND headless browsers.** Use `reddit-research` skill — it discovers posts via Brave Search and fetches content via Reddit's JSON API. Do not use `playwright` or `web_fetch` for Reddit; both get blocked.
- **Product Hunt pages frequently return 403.** If the builder mentions a PH launch, ask for the direct URL immediately rather than searching.

## Common Mistakes

- **Asking the builder to describe their product upfront** — the whole point is to show up informed. Do Layers 1 and 2 first, then present findings. Never start with "tell me about your product."
- **Asking open-ended questions** — "Tell me about your target market" is lazy. "We think your buyer is a mid-market engineering manager — is that right?" is useful.
- **Skipping Layer 2 because Layer 1 was rich** — the builder's own materials only show their perspective. Market signals show reality. Always do both.
- **Treating all sources equally** — a pitch deck is usually the most distilled articulation. A random blog post is lower signal. Weight accordingly.
- **Ignoring failed fetches** — if key pages couldn't be loaded, tell the builder and ask them to paste the content or provide an alternative.
- **Running too many search queries sequentially** — use `delegate` to parallelize research across competitor names, category keywords, and community platforms.
- **Presenting raw research instead of synthesis** — the builder doesn't want a list of links. They want assertions they can react to.
- **Not recording source URLs** — every claim in the product context must have a clickable URL, not just a source name. "Quora" is not a source. The URL to the specific Quora thread is.
- **Assuming the product name is unique in search** — always check for namespace collisions after the first search. Use the builder's URL as the primary anchor, not the product name.
- **Skipping the tool check** — defaulting to `web_fetch` when better tools are available means lower coverage. Always run Step 0a.
- **Silently skipping social listening or Reddit** — if X/Twitter or Reddit research was skipped, flag it explicitly to the builder. Every coverage gap must be visible, never silent.
- **Writing the document before builder validation** — present findings in the conversation first (Step 3). Write the document once after convergence (Step 6). This avoids a dozen incremental edits.
- **Researching the wrong buyer** — always validate the buyer hypothesis (Step 1.5) before Layer 2 research. Misdirected research wastes the most time.

## Known Fetch Issues

Common access failures and workarounds:

| Platform | Issue | Workaround |
|----------|-------|------------|
| Reddit | Blocks headless browsers AND web_fetch (403) | Use `reddit-research` skill — discovers via Brave Search, extracts via Reddit JSON API |
| Product Hunt | Frequently returns 403 | Ask builder for direct URL. Fallback: search for cached/indexed PH content |
| Medium | Paywall / 403 on many articles | Use `playwright` skill. Fallback: search for cached versions |
| LinkedIn | Aggressive bot detection, login-walled | Use `linkedin-research` skill — accesses content via search engine indexes (Gemini, xAI, Brave) |
| Quora | Dynamic content / 403 | Question titles from search are often sufficient signal |
| Twitter/X | Requires authentication | Use `xai-search` skill (curl to xAI API with `x_search` tool). Requires `XAI_API_KEY`. |
