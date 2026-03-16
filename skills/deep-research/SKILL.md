---
name: deep-research
description: "Research a topic in depth — searches the web, synthesizes findings, delivers a structured report"
tools: attach
---

# Deep Research

## Overview
Research a topic by decomposing it into sub-questions, searching the web for
each, synthesizing findings, and delivering a structured report as an attachment.

**Prerequisite skills:** Before starting, load this skill:
- `web-search` — how to search the web via Brave API

## Workflow

1. **Clarify scope** — If the request is vague, ask ONE question to narrow it
   (timeframe, angle, depth). If already clear, skip this step.

2. **Load prerequisite skills** — Use `load_skill` to load `web-search`.
   Read and understand it before proceeding.

3. **Decompose** — Break the topic into 3-5 research sub-questions:
   - Background and context
   - Current state of affairs
   - Key players, sources, or data points
   - Risks, controversies, or opposing views
   - Outlook and implications

4. **Research each sub-question** — For each sub-question:
   - Run 1-2 targeted web searches using the `web-search` skill
   - Extract the most relevant results (titles, URLs, descriptions)
   - Use `web_fetch` to read the full content of the most promising URLs
   - Take notes on key findings and source URLs

5. **Synthesize** — Compile findings into a structured report:

   ```
   ## Key Findings
   - [3-5 bullet summary of most important findings]

   ## [Sub-question 1 heading]
   [Findings with inline source citations]

   ## [Sub-question 2 heading]
   [Findings with inline source citations]

   ...

   ## Sources
   - [Title](URL) — what was found here
   ```

6. **Save and attach report** — Write the report to `workspace/reports/{topic-slug}-{YYYY-MM-DD}.md`,
   convert it to PDF with `pandoc report.md -o report.pdf`, then use the
   `attach` tool to attach the PDF. This delivers a nicely formatted report
   as a file alongside your reply (e.g. as an email attachment).

7. **Reply with summary** — Return a concise summary (key findings + bullet points)
   as your text response. The full report is delivered as the attachment.

## Tips
- Run multiple focused searches rather than one broad one.
- Use `freshness=pw` or `freshness=pm` when recency matters.
- Cite every claim — include the source URL inline.
- Use `jq` to parse search results efficiently.

## Common Mistakes
- Doing one big search instead of targeted queries per sub-question
- Forgetting to cite sources with URLs
- Not fetching actual page content — search snippets alone are too shallow (use `web_fetch`)
- Forgetting to use the `attach` tool after writing the report file
