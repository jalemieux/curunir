---
name: deep-research
description: "Use when asked to research a topic, produce a research report, investigate something requiring multiple sources, or conduct a comprehensive analysis. Trigger phrases include 'research X', 'research this', 'look into', 'investigate', 'deep research', or any request for a written report on a topic. The word 'deep' is not required. Do NOT use for investment requests whose deliverable is a buy/sell/hold recommendation or a ranking of investable names (stocks, ETFs, sectors, commodities, crypto) by return potential — even when phrased as 'investment research' or 'research the top N …'; defer to investment-memo for those."
portal_summary: "Research any topic into a sourced written report"
tools: attach
---

# Deep Research

Research a topic by decomposing it into sub-questions, selecting the right data sources, and delivering a structured report as a PDF attachment.

**Prerequisite skills** — always load `web-search`. Load additional sources based on the topic:
- `reddit-research` — community discussions, user reviews, sentiment
- `xai-search` — X/Twitter social listening, real-time reactions
- `gemini-search` — Google-grounded web search, YouTube summarization
- `linkedin-research` — professional context, company/founder research, job market

## Usage

### Tool selection rule

Two tools for fetching, each with a distinct role:

- **`curl + jq`** → API endpoints that return JSON (search APIs, Reddit `.json` endpoints). Requires headers, auth, or POST bodies.
- **`WebFetch`** → web pages that return HTML (articles, blogs, docs, landing pages from search results). Converts to markdown, summarizes large content, and accepts a `prompt` to extract only what's relevant — keeping context lean.

**The test:** if the URL returns JSON, use curl. If it returns a web page, use WebFetch with a targeted prompt. Never use curl to read web page content — it dumps raw HTML into context.

### Step 1 — Clarify and select sources

If the request is vague, ask ONE question to narrow scope (timeframe, angle, depth). Then select data sources using the Source Selection Reference below. Load each selected skill.

### Step 2 — Decompose

Break the topic into 3-5 research sub-questions:
- Background and context
- Current state of affairs
- Key players, sources, or data points
- Risks, controversies, or opposing views
- Outlook and implications

### Step 3 — Research each sub-question

For each sub-question:
- Pick the best data source(s) for that specific sub-question
- **Search** (curl): Run 1-2 targeted API searches per source using the relevant skill
- **Read** (WebFetch): Fetch full content of the most promising URLs using `WebFetch` with a prompt focused on the sub-question — e.g., `"Extract key findings about [topic], pricing, and user sentiment"`
- Take notes on key findings and source URLs

### Step 4 — Synthesize report

Compile findings into a structured report. The first page sets the frame — readers should know what they're looking at, who it's for, what question it answers, and whether it's been verified, before the first finding.

**Use this exact header template:**

```markdown
# {Descriptive title — long-form, not a slug}
## {Optional subtitle framing the question or angle}

**Date:** {Month DD, YYYY}

**Prepared for:** {user's name from identity context}

**Subject:** {One- to two-sentence framing of what the report investigates — the hypothesis, question, or scope}

**Status:** Draft — not yet independently fact-checked

## Executive Summary

{3–5 short paragraphs. Lead with what the report addresses and the headline finding in one sentence. Then: the strongest evidence for the answer, the strongest evidence against, key caveats or open questions, and a bottom-line "so what". Front-load the most important caveat — if the headline number comes from a narrow sub-population, a small sample, or a single source, say so here, not buried later.}

## Key Findings

- [3-5 bullet summary of the most load-bearing facts]

## {Sub-question 1 heading}
[Findings with inline source citations]

## {Sub-question 2 heading}
[Findings with inline source citations]

...

## Sources
- [Title](URL) — [Reddit] what was found here
- [Title](URL) — [X/Twitter] what was found here
```

**Title guidance.** The H1 should read like a magazine cover, not a filename: `"Retatrutide: Blockbuster or Bust? Investment Analysis for Eli Lilly (LLY)"` beats `"Retatrutide Research"`. The filename slug is separate (see Step 5).

**Prepared for.** Pull the user's name from `context/identity.md` or memory. If you don't know it, omit the line rather than guess.

**Subject vs. title.** Title is the headline; Subject is the one-sentence framing of what's being investigated (e.g., "Eli Lilly's investigational triple-agonist drug retatrutide — bull case, bear case, and potential impact on LLY stock"). They are not the same.

**Status line.** Start as `Draft — not yet independently fact-checked`. If the report is later updated with fact-check corrections (see below), change to `Fact-checked {YYYY-MM-DD} — corrections incorporated` and update the Date line to reflect the revision: `Date: May 4, 2026 (updated May 11, 2026 — fact-checked, corrected & expanded)`.

### Step 5 — Fact-check the draft (default, not optional)

Write the draft to `workspace/generated/{topic-slug}-{YYYY-MM-DD}.md`, then **run an independent fact-check before delivering**. Research without verification is just plausible-sounding prose. The whole point of the structured header is that "Status: Fact-checked" actually means something — which requires actually doing it.

Load `fact-checker` and follow its delegation pattern (the fact-checker skill explains why a fresh context window is the entire mechanism — you can't check yourself):

```python
delegate(task="""
Fact-check the research report below. Load the `fact-checker` skill and follow the
"Sub-agent workflow" section exactly. Return the structured report as your final response.

<<<CONTENT_TO_FACT_CHECK
[paste the full draft markdown content here]
CONTENT_TO_FACT_CHECK>>>
""")
```

For very large drafts (>50KB), write the content to `workspace/scratch/` first and tell the sub-agent the path.

When the sub-agent returns the verdicts report:

1. Apply each ❌ Contradicted and ⚠️ Partially accurate correction inline in the body — don't leave wrong numbers in the prose.
2. Update the **Date** line: `{original date} (updated {same or later date} — fact-checked, corrected & expanded)`.
3. Update the **Status** line: `Fact-checked {YYYY-MM-DD} — corrections incorporated`.
4. Append a `## Fact-Check Addendum` section after Sources, summarizing what changed:

```markdown
## Fact-Check Addendum ({Month DD, YYYY})

The following corrections were incorporated based on independent fact-checking:

| # | Issue | Correction |
|---|---|---|
| 1 | {What was wrong or unclear} | {What was changed} |
| 2 | ... | ... |
```

**Skip the fact-check only if** the user explicitly opts out ("skip fact-check", "quick summary, no verification"), or the topic is purely sentiment/opinion where there are no verifiable factual claims (e.g., "research how developers feel about X"). In the skip case, leave the Status line as `Draft — not independently fact-checked`.

**If `delegate` times out** (`Sub-agent timed out after 300s`), don't retry — the report is too broad to verify in one pass. Deliver the draft as-is, set Status to `Draft — fact-check timed out; scoped follow-up recommended`, and tell the user in the text reply.

### Step 6 — Deliver

Convert the final (fact-checked) markdown to PDF:

```bash
pandoc {file}.md -o {file}.pdf
```

Attach the **PDF**: `attach(path="{file}.pdf")`. If PDF conversion fails, attach the `.md` instead. Never convert to HTML or other formats.

Reply with a concise summary (key findings + bullets) as your text response. The full report is the attachment. If the fact-check surfaced material corrections, mention that in one line so the user knows the addendum is worth a glance.

## Examples

**Consumer product research** — user asks: "Research the current state of AI code editors"

1. Load `web-search`, `reddit-research`, `xai-search`
2. Sub-questions: market overview, top players and pricing, developer sentiment, limitations/complaints, outlook
3. Research: Brave for market reports and reviews → Reddit for r/programming and r/vscode discussions on Cursor/Copilot/etc → X for developer reactions to recent releases
4. Cross-reference: if review sites rate a tool highly but Reddit threads are full of complaints about reliability, highlight the contrast
5. Synthesize, tag sources, deliver PDF

**Company/market analysis** — user asks: "Research Stripe's competitive position in payments infrastructure"

1. Load `web-search`, `linkedin-research`, `gemini-search`, `xai-search`
2. Sub-questions: market share and positioning, leadership team, competitive landscape, developer sentiment, recent moves
3. Research: Brave + Gemini for market reports and analyst coverage → LinkedIn for exec backgrounds and hiring signals (job postings reveal strategic priorities) → X for developer opinions and reactions to Stripe announcements
4. Synthesize, tag sources, deliver PDF

## Reference

### Source Selection

Pick 2-3 sources that fit the topic. Don't use all sources indiscriminately.

| Topic signal | Add these sources | Why |
|---|---|---|
| Consumer product, app, game, tool | `reddit-research` | User reviews, complaints, feature requests |
| Breaking news, trending event | `xai-search` | Real-time reactions on X |
| Company, startup, industry analysis | `linkedin-research`, `gemini-search` | Founder backgrounds, job postings, Google's deep business index |
| Person research (founder, exec) | `linkedin-research`, `xai-search` | Professional history + public statements |
| Technical topic, developer tools | `reddit-research`, `xai-search` | Developer discussions + release reactions |
| Sentiment or public opinion | `reddit-research`, `xai-search` | Unfiltered opinions from both platforms |
| Market research, competitive intel | `gemini-search`, `linkedin-research` | Review sites, market data, job postings reveal tool adoption |
| YouTube / video content | `gemini-search` | Gemini summarizes YouTube videos directly |
| Job market, hiring trends | `linkedin-research` | Job postings are the primary signal |

**When in doubt:** opinions → `reddit-research` / `xai-search`. Business context → `linkedin-research`. Need Google's index → `gemini-search`.

### Source roles

- **Web search (Brave)** — backbone for every sub-question. Authoritative articles, news, reference.
- **Reddit / X** — "what real people think" layer. Sentiment, reviews, complaints, controversies.
- **LinkedIn** — professional layer. Key players, company positioning, hiring signals.
- **Gemini** — complementary Google-indexed search. Use when Brave results are thin, or for YouTube content.
- **Cross-reference** across sources — contrasts between official coverage and community sentiment are findings worth highlighting.

## Tips

- Run multiple focused searches per sub-question rather than one broad search.
- Use `freshness=pw` or `freshness=pm` when recency matters (Brave).
- Cite every claim with a source URL inline.
- Social sources (Reddit, X) are qualitative signal, not authoritative facts.

## Common Mistakes

- **One big search instead of targeted queries** — decompose into sub-questions, search each separately.
- **Search snippets without full content** — snippets are too shallow. Use `web_fetch` to read promising pages.
- **Missing source citations** — every claim needs an inline URL.
- **Attaching .md instead of .pdf** — always convert to PDF first. Only fall back to .md if pandoc fails.
- **Forgetting `attach()`** — the report file must be attached, not just written.
- **Using all sources on every topic** — match sources to the topic. A technical deep-dive doesn't need LinkedIn; a company analysis doesn't need Reddit.
- **Treating social opinions as facts** — Reddit/X posts are signal about sentiment, not authoritative sources. Cross-reference with web sources.
- **Not loading prerequisite skills** — load each skill before using its API patterns. The agent needs the skill's instructions to call APIs correctly.
- **Using curl to read web pages** — dumps raw HTML into context, causing context drift. Use `WebFetch` with a targeted prompt for page content. Reserve curl for JSON API endpoints only.
- **Filename slug as the H1** — the slug is for the file, not the reader. The H1 inside the document should be a long-form descriptive title.
- **Burying the caveat** — if the headline number depends on a narrow sub-population, small sample, or single source, that caveat belongs in the Executive Summary, not three sections later.
- **Skipping the header block** — Date / Prepared for / Subject / Status is not optional decoration; it tells the reader what they're holding before they read a word of the body.
- **Header lines without blank lines between them** — markdown collapses consecutive lines into one paragraph. Each `**Field:**` line in the header needs a blank line before the next, or pandoc renders them as one run-on sentence.
- **Skipping the fact-check pass** — it's the default, not optional decoration. The only valid reasons to skip are (a) the user explicitly opted out, or (b) the topic has no verifiable claims (pure sentiment/opinion). Otherwise: delegate to `fact-checker`, apply corrections, append the addendum.
- **Fact-checking yourself instead of delegating** — your context is already anchored on the same sources and framing you used to write the report. Always use `delegate` so the fact-checker gets a fresh window.
