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
3. Use `curl` to fetch the full content of the most relevant URLs
4. Synthesize findings across searches

## Tips

- Always use `$BRAVE_API_KEY` — never hardcode the token.
- Use `jq` to parse results — the raw JSON is verbose.
- For deep research, fetch the actual page content of promising URLs with `curl`.
- Use `freshness` to filter for recent results when timeliness matters.
- Run multiple targeted queries rather than one broad query for better coverage.
