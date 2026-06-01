---
name: gtm-competitive-monitor
description: "Use when a competitive landscape document exists and needs a delta scan for changes — surfaces new competitor moves, incumbent feature launches, pricing changes, and new entrants since the last check. Trigger: periodic check (manual or scheduled), or builder wants to know what's changed in the competitive landscape."
---

# Competitive Monitor

Periodic delta scan against an established competitive landscape. Reads the existing `competitive-landscape.md`, checks each tracked competitor and incumbent for changes since the last run, and surfaces what's new and what matters.

Goal: catch competitive moves (like Perplexity's Model Council) within days, not months.

**Requires:** An existing `competitive-landscape.md` from a prior `competitive-landscape` run. Same research tool stack as competitive-landscape. Can be invoked manually or scheduled via the `schedule` skill.

## Tool Priority

Same stack as competitive-landscape:

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

1. Probe for all research tools (same as competitive-landscape Step 0)
2. Read the `competitive-landscape.md` path — it must exist. If it doesn't, stop: "No competitive landscape document found at {path}. Run competitive-landscape first to establish the baseline."
3. Log tool availability for the monitor update section

### Step 1: Load Current State

Read `competitive-landscape.md`. Extract:

- **All tracked competitors** — name, type (direct/segment), last-known positioning, pricing, key features
- **All incumbent adjacents** from the Watchlist section — name, what to watch for
- **The product's core differentiators** from the Product Reference section
- **Date of last check** — from the most recent Monitor Update section's date. If no prior monitor runs, use the document's generation date from the header.
- **Product category and buyer segment** — needed for new entrant searches

### Step 2: Per-Competitor Delta Check

For each tracked competitor (direct and segment):

1. **Announcements** — search for blog posts, product updates, Product Hunt launches since last check. Queries:
   - "{competitor} new feature" (freshness: since last check or last 30 days, whichever is shorter)
   - "{competitor} update {month} {year}" for months since last check
   - "{competitor} launch" (freshness: recent)

2. **Pricing changes** — re-fetch pricing page. Compare to last-known pricing from the landscape doc. Note any changes in tiers, pricing model, free tier, or positioning.

3. **Feature launches** — for any new features found, assess overlap with the product's core differentiators.

4. **Community sentiment shifts** — quick scan for new complaint patterns, viral praise, or "switched to/from" mentions:
   - reddit-research: "{competitor}" (freshness: recent)
   - xai-search: "{competitor}" (recent mentions)

5. **Flag each competitor:** `no change`, `minor update` (pricing tweak, small feature), or `material change` (new feature that changes competitive dynamics, major pivot, pricing overhaul).

### Step 3: Per-Incumbent Delta Check

For each incumbent on the Watchlist:

1. **Blog/changelog scan** — search for new features since last check:
   - "{incumbent} new features {year}"
   - "{incumbent} changelog"
   - "{incumbent} announcing" OR "{incumbent} launch" (freshness: since last check)

2. **Social announcements** — xai-search for official account posts about new features; gemini-search for product updates

3. **Community reactions** — reddit-research and xai-search for user threads about recent incumbent launches

4. **Feature overlap assessment** — for each new feature found:
   - Does this overlap with the product's core differentiators?
   - Rate: `no overlap` / `partial overlap` / `direct overlap`
   - If direct overlap: this is a **high-priority alert** — flag prominently in the update

### Step 4: New Entrant Scan

Quick search for new players that didn't exist at last check:

- "new {category} tool {year}" (freshness: since last check)
- "{category} launch {year}" (freshness: recent)
- Product Hunt: search for recent launches in the category
- xai-search: "just launched {category}" OR "new {category} tool" (recent, high engagement)

For each new entrant found:
- What it does
- How it overlaps with the product
- Initial threat assessment: `watch` (add to landscape) or `ignore` (not relevant)

### Step 5: Write Update

**Append** a dated section to the end of `competitive-landscape.md`, inside the `## Monitor Updates` section:

```markdown
### Monitor Update — {date}

**Period:** {last check date} → {today}
**Tools available:** {list of available research tools}

#### Material Changes
- **{Competitor/incumbent}:** {what changed} — {why it matters for the product} — [{source}]({URL})

#### Minor Updates
- **{Competitor}:** {small change, low impact} — [{source}]({URL})

#### New Entrants
- **{New tool}:** {what it does, initial assessment} — [{source}]({URL})

#### No Change
- {List of competitors/incumbents with no detected changes}

#### Sources
| Source Type | Query / URL | Finding |
|------------|------------|---------|
| {tool used} | {query or URL} | {what was found} |
```

**Do not modify** any sections above the Monitor Updates section. The monitor only appends.

**Produce a conversation summary** after writing the update:
- Lead with material changes (if any)
- Call out any incumbent moves that overlap core differentiators — this is the highest-priority signal
- Note new entrants worth adding to the watchlist
- State "no material changes" if the landscape is stable
- If changes are large enough to warrant a full re-analysis, recommend re-running competitive-landscape

## Scheduling

This skill works identically whether invoked manually or via the `schedule` skill:

- **Manual:** Builder invokes when they want a refresh
- **Scheduled:** Set up via `schedule` skill with a cron expression (e.g., weekly on Monday). The scheduled invocation passes the path to `competitive-landscape.md`.

**Recommended cadence:**
- Weekly for fast-moving markets (AI, SaaS, crypto)
- Biweekly for moderate markets (B2B software, developer tools)
- Monthly for slow-moving markets (enterprise, regulated industries)

## Tips

- The monitor is designed to be lightweight — it should complete in a fraction of the time competitive-landscape takes. Don't re-research everything; focus on what's changed.
- Incumbent delta checks are the highest-priority step. If you're short on time or tools, prioritize incumbents over direct competitors — incumbents shipping overlapping features is the existential risk.
- The "no change" list is important — it tells the builder that the landscape was checked, not just that nothing was reported.
- When a monitor run surfaces a material change, the conversation summary should be actionable: not just "Perplexity launched Model Council" but "Perplexity launched Model Council — multi-model deliberation on their $200/mo tier. This directly overlaps your core debate mechanic."

## Common Mistakes

- **Modifying the landscape document's main sections** — the monitor only appends to the Monitor Updates section. If the landscape needs a full rewrite, recommend re-running competitive-landscape instead.
- **Skipping the incumbent check** — the whole point of the monitor is to catch incumbent moves. This is never optional.
- **Not including "no change" entries** — silence is ambiguous. Did you check and find nothing, or did you not check? Always list what was checked.
- **Burying material changes in a long list** — lead the conversation summary with the most important finding. If an incumbent shipped a directly overlapping feature, that's the headline.
- **Running too broad a new entrant scan** — this should be a quick check, not a full discovery pass. If many new entrants are found, it may signal the category is heating up — note this in the summary and recommend a full competitive-landscape re-run.
