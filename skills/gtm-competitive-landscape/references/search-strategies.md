# Competitive Landscape — Search Strategies

Query patterns for each research phase. Template variables: `{product}`, `{category}`, `{competitor}`, `{incumbent}`, `{pain point}`, `{buyer role}`, `{industry}`, `{year}`.

## Phase 1: Competitor Discovery

### Track A — Direct Competitors

**Category searches:**
- "best {category} tools {year}"
- "{category} comparison {year}"
- "{product} alternatives"
- "{product} vs"
- "{category} software"

**Problem-keyword searches:**
- "{pain point} solution"
- "{pain point} tool"
- "how to {job-to-be-done}"
- "{job-to-be-done} software"

**Review platform discovery:**
- "site:g2.com {category}"
- "site:capterra.com {category}"
- "site:producthunt.com {category}"
- "site:trustradius.com {category}"

**Community discovery:**
- "site:reddit.com best {category}" (via reddit-research skill)
- "site:reddit.com {category} recommendation" (via reddit-research skill)
- xai-search: "{category} tool recommendation"

### Track B — Buyer-Segment Competitors

Adapt queries based on the confirmed buyer hypothesis. The buyer's language is often different from the category language.

- "{buyer role} tools for {pain point}"
- "best {category} for {industry}"
- "{buyer role} {pain point} solution"
- "what do {buyer role}s use for {job-to-be-done}"
- xai-search: "{buyer role} AI tools" or "{buyer role} {category}"

### Track C — Incumbent Adjacents

Identify from product context's "Current solution" and "Competitive Landscape" fields. These are large products the buyer already uses. Search for each:

- "{incumbent} features {year}"
- "{incumbent} what's new"
- "{incumbent}" on Product Hunt (recent launches)

## Phase 2: Deep Research Per Competitor

For each competitor identified in Phase 1:

**Positioning & messaging:**
- Fetch landing page via playwright or web_fetch
- Note: tagline, hero copy, target audience claim, key feature claims

**Pricing:**
- Fetch pricing page: "{competitor} pricing"
- Note: tiers, free tier, BYOK, credit system, annual discount, "contact sales"
- If pricing page is gated: search "{competitor} pricing {year}" for cached info

**Community sentiment:**
- reddit-research: "{competitor} review" OR "{competitor} experience" OR "{competitor} vs"
- xai-search: "{competitor} review" (X/Twitter)
- xai-search: "switched from {competitor}" OR "switched to {competitor}"
- Brave/Gemini: "site:g2.com {competitor}" OR "site:capterra.com {competitor}"

**Review platforms:**
- G2: "site:g2.com/products/{competitor-slug}"
- Capterra: "site:capterra.com/software/{competitor-slug}"
- Product Hunt: "site:producthunt.com/products/{competitor-slug}"
- Trustpilot: "site:trustpilot.com/review/{competitor-domain}"

**Content & SEO:**
- "{competitor} blog"
- Check if competitor ranks for buyer-intent keywords from Phase 1

## Phase 3: Incumbent Feature Watch

**This is the critical phase that prevents the "Perplexity Model Council" miss.** For each incumbent adjacent:

**Blog/changelog scan (last 90 days):**
- "{incumbent} new features {year}"
- "{incumbent} changelog {year}"
- "{incumbent} blog" → fetch and scan for product announcements
- "{incumbent} launch" (freshness: last 90 days)
- "{incumbent} update {month} {year}" for each of the last 3 months

**Social announcements:**
- xai-search: "from:{incumbent_handle} new feature" OR "from:{incumbent_handle} announcing" OR "from:{incumbent_handle} launch"
- gemini-search: "{incumbent} LinkedIn announcement {year}"
- gemini-search: "{incumbent} product update {year}"

**Community reactions:**
- reddit-research: "{incumbent} new feature" OR "{incumbent} update" (freshness: recent)
- xai-search: "{incumbent} new feature" (X/Twitter reactions)
- "site:news.ycombinator.com {incumbent}" (recent HN discussions)

**Feature overlap assessment:**
For each new feature found, answer:
1. Does this feature overlap with the product's core differentiators?
2. Rate: `no overlap` / `partial overlap` / `direct overlap`
3. If partial or direct: how does the incumbent's implementation compare? (pricing tier, depth, accessibility)

## Phase 4: Market Signals

**Funding & hiring:**
- gemini-search: "{competitor} funding {year}" OR "{competitor} raised"
- linkedin-research: "{competitor} hiring" → look for roles suggesting expansion into the space
- gemini-search: "{category} startup funding {year}"

**SEO positioning:**
- Search each buyer-intent keyword from Phase 1 and note who ranks in top 5
- gemini-search: "{category} market size {year}" OR "{category} market trends"

## Platform-Specific Notes

| Platform | Issue | Workaround |
|----------|-------|------------|
| Reddit | Blocks web_fetch AND headless browsers (403) | Use `reddit-research` skill — discovers via Brave Search, extracts via Reddit JSON API |
| Product Hunt | Frequently returns 403 | Ask builder for direct URL after 2 failed attempts. Fallback: search for cached/indexed PH content |
| LinkedIn | Login-walled | Use `linkedin-research` skill — accesses content via search engine indexes |
| Twitter/X | Requires authentication | Use `xai-search` skill (curl to xAI API). Requires `XAI_API_KEY`. |
| G2/Capterra | Sometimes block automated access | Search for cached content via gemini-search or web-search |

## Fetch Failure Escalation

When a fetch fails (403, redirect loop, JS-rendered):
1. Retry once with a different tool (e.g., `playwright` if `web_fetch` failed)
2. If still fails after 2 attempts: **ask the builder for the direct URL or content**
3. Don't burn 5+ searches — the builder often knows where competitive intel lives
4. Note all failed fetches for the Tool Coverage section
