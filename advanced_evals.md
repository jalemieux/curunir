# Advanced Skill Evals

Eval prompts that exercise Curunir's multi-step skills: web search, deep research,
content extraction, delegation, and cross-skill orchestration. These require live
API keys (BRAVE_API_KEY at minimum) and network access.

---

## Skill Loading

```
Load the web-search skill, then tell me what tools and API it uses. Do not run any searches yet.
```

```
Load the deep-research skill and list every prerequisite skill it mentions. Do not load them yet.
```

```
Load a skill that does not exist called "fake-nonexistent-skill" and handle the error gracefully.
```

---

## Web Search — Basic

```
Search the web for "Rust async runtime comparison 2026" and give me the top 5 results with titles and URLs.
```

```
Search for recent news about OpenAI using the freshness parameter to limit results to the past week. Summarize the top 3 stories.
```

```
Search for "site:arxiv.org transformer architecture improvements" and list any papers from 2026.
```

---

## Web Search — Content Extraction

```
Search for "Python 3.13 new features" and then use WebFetch to read the most relevant result. Summarize the key changes in under 150 words.
```

```
Search for the Brave Search API pricing page, then fetch it with WebFetch and extract the current pricing tiers.
```

```
Find a recent blog post about LLM evaluation frameworks, fetch it, and give me the author's main argument in 2-3 sentences.
```

---

## Web Search — Multi-Query Research

```
I want to understand the current state of WebAssembly outside the browser. Run at least 3 different searches targeting: server-side WASM runtimes, WASM in edge computing, and WASM for plugin systems. Synthesize your findings.
```

```
Compare the developer sentiment around Bun vs Deno in 2026. Search for both, read a couple of results for each, and highlight where opinions diverge.
```

---

## Deep Research — Planning

```
I want to research the current state of AI code editors. Don't run any searches yet — just show me how you'd decompose this into sub-questions and which data sources you'd select from the deep-research skill.
```

```
Plan a deep research report on "the impact of EU AI Act on open source LLMs". Show me your sub-questions, source selection, and reasoning. Do not execute yet.
```

---

## Deep Research — Execution

```
Research what developers think about Cursor vs GitHub Copilot in 2026. Use web search and Reddit. Produce a short report (under 500 words) with source citations. Skip the PDF — just give me the markdown.
```

```
Do a quick research pass on "passkeys adoption status 2026" — web search only, no social sources. Give me 5 key findings with source URLs.
```

---

## Cross-Skill Orchestration

```
Load web-search, then search for "shot-scraper CLI tutorial". From the results, pick the best URL and fetch it with WebFetch. Summarize how shot-scraper works in 3 bullet points.
```

```
I need to understand how Reddit's .json API works. Load the reddit-research skill, then search the web for documentation on Reddit's public JSON endpoints. Fetch and summarize the most useful result.
```

---

## Delegation

```
Delegate this task to a sub-agent: "Search the web for the top 3 Python testing frameworks in 2026 and list them with one-sentence descriptions." Report back what the sub-agent found.
```

```
Delegate two independent research tasks: (1) search for "SQLite extensions for vector search" and (2) search for "DuckDB vs SQLite performance benchmarks 2026". Summarize both results.
```

---

## Error Handling and Edge Cases

```
Search the web for an extremely obscure query that will likely return no results: "xyzzy_plugh_42_does_not_exist_anywhere". How do you handle zero results?
```

```
Load web-search and attempt to fetch a URL that will 404: https://httpstat.us/404. Handle the failure and explain what happened.
```

```
Search for "Python asyncio" but ask for 50 results (the Brave API max is 20). What happens?
```

---

## Tool Selection Judgment

```
I want to know what port Curunir runs on. Should you search the web or read a local file? Do whichever is correct.
```

```
What's the current population of Tokyo? Use the most appropriate tool to answer this.
```

```
How does the delegate tool work in this codebase? Use the most appropriate tool — do not search the web for this.
```

---

## Output Quality Under Constraints

```
Research "quantum computing progress 2026" using web search. Summarize in exactly 3 bullet points, each under 20 words. Cite one URL per bullet.
```

```
Search for "best practices for LLM system prompts" and write a one-paragraph summary aimed at someone who has never used an LLM. No jargon.
```

```
Find the latest stable release version of Node.js. Answer with just the version number and release date — nothing else.
```
