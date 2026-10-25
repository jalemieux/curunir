---
name: web-search
description: "Search the web using Brave Search API via bash — returns titles, URLs, descriptions"
---

# Web Search

Use the Brave Search API via `curl` in the bash tool.
The API key is in the `BRAVE_API_KEY` environment variable.

## Basic Search

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=YOUR+QUERY" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY"
```

Always URL-encode the query string (`+` for spaces, `%20` also works).

## Useful Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `q` | required | Search query |
| `count` | 20 | Results per page (max 20) |
| `offset` | 0 | Pagination offset |
| `freshness` | none | `pd` (24h), `pw` (7 days), `pm` (31 days), `py` (1 year) |
| `country` | none | 2-char country code (e.g. `US`, `GB`) |
| `extra_snippets` | false | Get up to 5 additional excerpts per result |

Example with parameters:

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=climate+policy+2026&count=10&freshness=pm" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY"
```

## Reading Results

Results are in `(.web.results // [])[]`. Each result has:

- `title` — page title
- `url` — link
- `description` — snippet/excerpt

Use `jq` to extract what you need:

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=YOUR+QUERY" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  | jq '(.web.results // [])[] | {title, url, description}'
```

The `// []` guard is important: when Brave returns no `.web.results` (zero
hits, a spellcheck-altered query, or an error/quota payload), `.web.results[]`
would abort with `Cannot iterate over null`. The guarded form prints nothing
instead. **An empty result set printing nothing is normal — treat it as "no
results," not a tool failure, and don't retry the same query.**

## Search Operators

Brave supports standard search operators in the query string:

- `"exact phrase"` — exact match
- `site:example.com` — restrict to domain
- `filetype:pdf` — restrict to file type
- `-exclude` — exclude term

Example:

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=site%3Areuters.com+AI+regulation" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  | jq '(.web.results // [])[] | {title, url, description}'
```

## Research Pattern

When researching a topic, run multiple focused searches rather than one broad one.
For each search:

1. Search with a specific query
2. Extract titles, URLs, and descriptions with `jq`
3. Read full content from the most relevant URLs (see Content Extraction below)
4. Synthesize findings across searches

## Content Extraction

**Rule: curl is for search APIs (JSON). WebFetch is for reading pages (HTML).**

After finding URLs via search, use `WebFetch` to read page content — not curl. WebFetch converts HTML to markdown, strips ads/navigation, and accepts a `prompt` parameter to extract only relevant information. This keeps context lean during multi-source research.

```
WebFetch(url="https://example.com/article", prompt="Extract key findings about [topic], pricing details, and competitive positioning")
```

Use a targeted prompt — "extract X, Y, and Z" — instead of reading the full page. This returns a fraction of what raw page content would, preventing context drift in deep research sessions.

Fall back to the `playwright` skill (`shot-scraper`) when:
- The page is JS-rendered (SPAs, dashboards, dynamic content)
- `WebFetch` returns empty or incomplete content
- You need to interact with the page (scroll, click, wait for elements)

Never use raw `curl` for page content — it dumps full HTML with CSS, JS, and navigation into context.

## Tips

- Always use `$BRAVE_API_KEY` — never hardcode the token.
- Use `jq` to parse results — the raw JSON is verbose.
- Use `web_fetch` for content extraction by default — it returns clean text, no HTML.
- Fall back to `playwright` skill only for JS-rendered pages or when `web_fetch` returns incomplete content.
- Use `freshness` to filter for recent results when timeliness matters.
- Run multiple targeted queries rather than one broad query for better coverage.

## Common Mistakes

- **Using `curl` to read page content** — dumps raw HTML into context, causing context drift in research sessions. Use `WebFetch` with a targeted prompt instead. curl is only for API endpoints that return JSON.
- **Using `playwright` for every page** — it launches Chromium, which is slow. Reserve it for JS-rendered content. Most pages work fine with `web_fetch`.
- **Not URL-encoding search queries** — spaces and special characters in the Brave Search `q` parameter must be URL-encoded (`+` for spaces, `%3A` for `:`).
