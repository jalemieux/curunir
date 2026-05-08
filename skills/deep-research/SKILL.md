---
name: deep-research
description: "Use when asked to research a topic in depth, produce a research report, or investigate something requiring multiple sources. Trigger: user asks for deep research, comprehensive analysis, or a written report on a topic."
tools: attach, delegate
---

# Deep Research

Research a topic by decomposing it into sub-questions, **delegating each sub-question to a sub-agent**, then synthesizing the returned findings into a structured report delivered as a PDF attachment.

**Why delegation:** every search result and `web_fetch` page is large. If you run them in your own context, the report you can produce gets shorter as research progresses. Sub-agents do the searching and reading in their own context windows and return only curated findings — your context stays clean for synthesis.

**Available source skills** (sub-agents load these as needed):
- `web-search` — backbone for every sub-question (always include)
- `reddit-research` — community discussions, user reviews, sentiment
- `xai-search` — X/Twitter social listening, real-time reactions
- `gemini-search` — Google-grounded web search, YouTube summarization
- `linkedin-research` — professional context, company/founder research, job market

## Usage

### Step 1 — Clarify and select sources

If the request is vague, ask ONE question to narrow scope (timeframe, angle, depth). Then pick 2-3 source skills from the Source Selection Reference below — these are the skills your sub-agents will load.

You do **not** load these skills yourself. Sub-agents load them.

### Step 2 — Decompose

Break the topic into 3-5 research sub-questions:
- Background and context
- Current state of affairs
- Key players, sources, or data points
- Risks, controversies, or opposing views
- Outlook and implications

### Step 3 — Delegate each sub-question

For each sub-question, call `delegate(task=...)` with a self-contained prompt. The sub-agent runs searches and reads pages in its own context, then returns only curated findings.

Prompt template:

```
Research this sub-question: {sub-question}

Context: this is part of a larger report on {topic}. Other sub-questions
cover {brief list} — focus only on {this sub-question}.

Load these skills first (use load_skill for each): {comma-separated skill names}

Run targeted searches, read the most promising pages with web_fetch, and
cross-reference across sources. Return findings in this format:

## Key Findings
- 3-5 bullets capturing what matters

## Details
Prose with inline source citations like [Reddit](URL) or [Web](URL).
Tag every URL with its origin: [Reddit], [X/Twitter], [LinkedIn], [Web], [Gemini].

## Sources
- [Title](URL) — [Origin] one-line description of what this source contributed
```

Do **not** run searches or `web_fetch` calls yourself. Your job at this step is to delegate, wait, and collect.

### Step 4 — Synthesize report

Compile findings from all sub-agents into a structured report. Tag each source with its origin (`[Reddit]`, `[X/Twitter]`, `[LinkedIn]`, `[Web]`, `[Gemini]`):

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

1. Pick sources: `web-search`, `reddit-research`, `xai-search`
2. Sub-questions: market overview, top players and pricing, developer sentiment, limitations/complaints, outlook
3. Delegate each sub-question. Example for sentiment:

   ```
   delegate(task="Research this sub-question: developer sentiment on AI code
   editors (Cursor, Copilot, Cody, Windsurf, etc.) in 2025-2026.
   Context: part of a report on AI code editors. Other sub-questions cover
   market overview, top players, limitations, outlook — focus only on
   sentiment here.
   Load these skills first: web-search, reddit-research, xai-search.
   Search r/programming and r/vscode for recurring complaints and praise.
   Cross-reference with X reactions to recent releases.
   Return findings as: Key Findings (3-5 bullets), Details (prose with
   inline citations), Sources (URL list with [Reddit]/[X]/[Web] tags).")
   ```

4. Wait for each sub-agent to return findings, then synthesize into the report.
5. Convert to PDF, attach.

**Company/market analysis** — user asks: "Research Stripe's competitive position in payments infrastructure"

1. Pick sources: `web-search`, `linkedin-research`, `gemini-search`, `xai-search`
2. Sub-questions: market share and positioning, leadership team, competitive landscape, developer sentiment, recent moves
3. Delegate each sub-question with the relevant skills (e.g. leadership-team sub-agent loads `linkedin-research` + `web-search`; developer-sentiment sub-agent loads `xai-search` + `reddit-research`).
4. Synthesize, tag sources, deliver PDF.

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

- One `delegate` call per sub-question. The sub-agent owns search-and-read for that sub-question end-to-end.
- Make each delegation prompt self-contained: a sub-agent only sees the prompt, not your conversation history. Spell out the topic, the specific sub-question, the skills to load, and the expected output format.
- Sub-agents return curated findings, not raw page contents. If a returned answer is too thin, delegate a follow-up with sharper instructions.
- Cite every claim with a source URL inline.
- Social sources (Reddit, X) are qualitative signal, not authoritative facts.

## Common Mistakes

- **Running searches in your own context instead of delegating** — every search result and page fetch you read directly fills your context. Use `delegate` for each sub-question and let sub-agents do the reading.
- **Vague delegation prompts** — "research X" is not enough. State the sub-question, list the skills to load, specify the output format.
- **Forgetting to tell the sub-agent which skills to load** — sub-agents start with no skills loaded. The prompt must list every skill name.
- **One big delegation instead of one per sub-question** — splitting by sub-question keeps each sub-agent focused and the returned findings tight.
- **Search snippets without full content** — when delegating, instruct the sub-agent to use `web_fetch` to read promising pages, not just rely on snippets.
- **Missing source citations** — every claim needs an inline URL. Require this in the delegation prompt's output format.
- **Attaching .md instead of .pdf** — always convert to PDF first. Only fall back to .md if pandoc fails.
- **Forgetting `attach()`** — the report file must be attached, not just written.
- **Using all sources on every topic** — match sources to the topic. A technical deep-dive doesn't need LinkedIn; a company analysis doesn't need Reddit.
- **Treating social opinions as facts** — Reddit/X posts are signal about sentiment, not authoritative sources. Cross-reference with web sources.
