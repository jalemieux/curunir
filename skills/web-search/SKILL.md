---
name: web-search
description: "Search the web using Brave Search API via bash — returns titles, URLs, descriptions. Use FIRST for consumer/local-business sites that block scraping (Google, Yelp, Reddit, local reviews); don't web_fetch or curl those — they return 403/anti-bot pages. Brave already indexes their snippets."
---

# Web Search

Use the Brave Search API via `curl` in the bash tool.
The API key is in the `BRAVE_API_KEY` environment variable.

## Sites that block scraping — search first, don't fetch

Many high-value consumer and local-business sites actively block automated
access. Raw `curl` or `web_fetch` against them returns a `403`, a CAPTCHA, or an
anti-bot interstitial — not the content. **Don't fetch these; search Brave
instead.** Brave already indexes the very snippets (ratings, hours, top reviews,
thread answers) the request usually wants, so a single search answers most
"find me a salon / restaurant / what-do-people-say" lookups.

Known-blocked domains (treat as guidance, not an exhaustive denylist):

- `google.com/search` — raw scraping of Google results is blocked; use the
  Brave API for search instead of curling Google.
- `yelp.com` — local-business listings/reviews return anti-bot pages.
- `reddit.com` (including the `.json` endpoint) — blocks automated access.

Pattern for local-business / consumer lookups (e.g. "find a hair salon near San
Mateo"): **search Brave first**, read the titles/URLs/descriptions it returns,
then `web_fetch` only specific result URLs that aren't on the blocklist (a
salon's own site, a news article). Don't open with a `curl`/`web_fetch` of
Google/Yelp/Reddit — that's the rediscovery loop this skill exists to skip.

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

Results are in `.web.results[]`. Each result has:

- `title` — page title
- `url` — link
- `description` — snippet/excerpt

Use `jq` to extract what you need:

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=YOUR+QUERY" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  | jq '.web.results[] | {title, url, description}'
```

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
  | jq '.web.results[] | {title, url, description}'
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
