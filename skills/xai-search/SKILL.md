---
name: xai-search
description: "Use when you need to search the web or X/Twitter via the xAI API. Trigger: a skill or task requires web search or social listening through Grok, or the agent needs real-time X/Twitter data."
portal_summary: "Search the web and X/Twitter via Grok"
---

# xAI Search

Search the web and X/Twitter via the xAI Responses API. Requires `XAI_API_KEY` env var and `curl`/`jq`.

## Usage

**Endpoint:** `POST https://api.x.ai/v1/responses`

Two search tools are available — use one or both per request:

### Web Search

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "YOUR QUERY HERE"}],
    "tools": [{"type": "web_search"}]
  }' | jq .
```

### X/Twitter Search

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "YOUR QUERY HERE"}],
    "tools": [{"type": "x_search"}]
  }' | jq .
```

### Both Together

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "YOUR QUERY HERE"}],
    "tools": [{"type": "web_search"}, {"type": "x_search"}]
  }' | jq .
```

### Extracting Text + Citations

The response contains an `output` array and a `citations` array. Extract the text and sources:

```bash
# Text content only
... | jq -r '.output[] | select(.type == "message") | .content[] | select(.type == "output_text") | .text'

# Citations (URLs with titles)
... | jq -r '.citations[] | "- [\(.title // "source")](\(.url))"'
```

## Tool Parameters

### web_search

| Parameter | Type | Description |
|-----------|------|-------------|
| `allowed_domains` | string[] (max 5) | Only search these domains. Mutually exclusive with `excluded_domains`. |
| `excluded_domains` | string[] (max 5) | Exclude these domains. Mutually exclusive with `allowed_domains`. |

```json
{"type": "web_search", "allowed_domains": ["reddit.com", "news.ycombinator.com"]}
```

### x_search

| Parameter | Type | Description |
|-----------|------|-------------|
| `allowed_x_handles` | string[] (max 10) | Only search posts from these handles. Mutually exclusive with `excluded_x_handles`. |
| `excluded_x_handles` | string[] (max 10) | Exclude posts from these handles. |
| `from_date` | string (YYYY-MM-DD) | Start date for search window. |
| `to_date` | string (YYYY-MM-DD) | End date for search window. |

```json
{"type": "x_search", "from_date": "2025-01-01", "to_date": "2025-03-01"}
```

## Models

| Model | Cost (in/out per 1M tokens) | Use when |
|-------|----------------------------|----------|
| `grok-4-1-fast-non-reasoning` | $0.20 / $0.50 | Default — simple search queries |
| `grok-4-1-fast-reasoning` | $0.20 / $0.50 | Need reasoning over search results |
| `grok-4.20-reasoning` | $2.00 / $6.00 | Complex multi-step research |

All models have 2M token context windows.

## Examples

**Social listening for a product:**

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "\"WordSnap\" OR \"wordsnap.ai\" — what are people saying about this product? Include complaints, praise, and feature requests."}],
    "tools": [{"type": "x_search"}]
  }' | jq .
```

**Competitor research on X with date range:**

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "\"Grammarly\" complaint OR \"switched from\" OR issue — recent user frustrations"}],
    "tools": [{"type": "x_search", "from_date": "2025-01-01"}]
  }' | jq .
```

**Web search scoped to specific domains:**

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "best writing tools for ESL learners 2025"}],
    "tools": [{"type": "web_search", "allowed_domains": ["reddit.com", "g2.com", "capterra.com"]}]
  }' | jq .
```

## Tips

- The model makes follow-up search queries autonomously — you don't need to break a broad question into multiple API calls. One well-phrased query often suffices.
- Use `x_search` for social listening and real-time sentiment. Use `web_search` for market research, competitor pages, and review sites.
- Citations are always included in the response — no extra parameter needed.
- For GTM research, prefer `x_search` alone for Twitter data (faster, cheaper) and `web_search` alone for everything else. Combine both only when you need a unified answer across sources.

## Common Mistakes

- **Using `/v1/chat/completions` for search** — the old `search_parameters` on chat completions returns 410 Gone. Always use `/v1/responses`.
- **Combining `allowed_domains` and `excluded_domains`** — they're mutually exclusive. Same for `allowed_x_handles` and `excluded_x_handles`. Pick one or neither.
- **Forgetting to check `XAI_API_KEY`** — verify the env var is set before calling. A missing key returns a 401 that's easy to misdiagnose.
- **Over-querying** — the model does iterative search internally. One query like "What are people saying about X on Twitter?" is better than five narrow queries. Let Grok handle the fan-out.
