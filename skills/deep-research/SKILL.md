---
name: deep-research
description: "Use when asked to research a topic in depth, produce a research report, or investigate something requiring multiple sources. Trigger: user asks for deep research, comprehensive analysis, or a written report on a topic."
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
- Run 1-2 targeted searches per source using the relevant skill
- Use `web_fetch` to read full content of the most promising URLs
- Take notes on key findings and source URLs

### Step 4 — Synthesize report

Compile findings into a structured report. Tag each source with its origin (`[Reddit]`, `[X/Twitter]`, `[LinkedIn]`, `[Web]`):

```
## Key Findings
- [3-5 bullet summary]

## [Sub-question 1 heading]
[Findings with inline source citations]

## [Sub-question 2 heading]
[Findings with inline source citations]

...

## Sources
- [Title](URL) — [Reddit] what was found here
- [Title](URL) — [X/Twitter] what was found here
```

### Step 5 — Deliver

Write to `workspace/reports/{topic-slug}-{YYYY-MM-DD}.md`, convert to PDF:

```bash
pandoc {file}.md -o {file}.pdf
```

Attach the **PDF**: `attach(path="{file}.pdf")`. If PDF conversion fails, attach the `.md` instead. Never convert to HTML or other formats.

Reply with a concise summary (key findings + bullets) as your text response. The full report is the attachment.

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
