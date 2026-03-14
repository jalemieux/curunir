---
name: deep-research
description: "Research a topic in depth — searches the web, synthesizes findings, emails a structured report"
---

# Deep Research

## Overview
Research a topic by decomposing it into sub-questions, searching the web for
each, synthesizing findings, and emailing a structured report.

**Prerequisite skills:** Before starting, load these skills:
- `web-search` — how to search the web via Brave API
- `email-send` — how to send the report via gog CLI

## Workflow

1. **Clarify scope** — If the request is vague, ask ONE question to narrow it
   (timeframe, angle, depth). If already clear, skip this step.

2. **Load prerequisite skills** — Use `load_skill` to load `web-search` and
   `email-send`. Read and understand both before proceeding.

3. **Decompose** — Break the topic into 3-5 research sub-questions:
   - Background and context
   - Current state of affairs
   - Key players, sources, or data points
   - Risks, controversies, or opposing views
   - Outlook and implications

4. **Research each sub-question** — For each sub-question:
   - Run 1-2 targeted web searches using the `web-search` skill
   - Extract the most relevant results (titles, URLs, descriptions)
   - Fetch full page content from the most promising URLs with `curl`
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

6. **Save report** — Write the report to `workspace/reports/{topic-slug}-{YYYY-MM-DD}.md`

7. **Email report** — Using the `email-send` skill, send the report to the user.
   Use `--body-file` for the report content and set the subject to
   `Research Report: [Topic]`.

8. **Confirm** — Tell the user: "Research complete. Report emailed and saved to
   workspace/reports/."

## Tips
- Run multiple focused searches rather than one broad one.
- Use `freshness=pw` or `freshness=pm` when recency matters.
- Cite every claim — include the source URL inline.
- Use `jq` to parse search results efficiently.
- Write the report to a temp file first, then use `--body-file` to email it
  to avoid shell quoting issues with long content.

## Common Mistakes
- Doing one big search instead of targeted queries per sub-question
- Forgetting to cite sources with URLs
- Not fetching actual page content — search snippets alone are too shallow
- Trying to email the report inline instead of using `--body-file`
