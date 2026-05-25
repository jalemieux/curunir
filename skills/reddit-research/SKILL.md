---
name: reddit-research
description: "Use when you need Reddit discussions, reviews, complaints, or community sentiment for product research. Trigger: an agent needs to find what real users are saying about a product, competitor, pain point, or topic on Reddit."
portal_summary: "Find what real users on Reddit say about a product or topic"
---

# Reddit Research

Discover and extract Reddit posts and comments for product and market research. Two-step pipeline: find posts via Brave Search, then fetch full content via Reddit's public JSON API.

Requires `BRAVE_API_KEY` env var, `curl`, and `jq`.

## Why Not Playwright

Reddit blocks headless browsers at the network level. User agent spoofing does not help — Reddit detects and rejects automated browser traffic before any content loads. This skill bypasses the block entirely by using Brave Search for discovery and Reddit's own `.json` API for extraction. No browser required.

## Step 1 — Discovery

### Brave Search (primary)

Use `site:reddit.com` to scope Brave Search results to Reddit posts:

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=site%3Areddit.com+YOUR+QUERY&count=10" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  | jq -r '.web.results[] | {title, url}'
```

### xAI web_search with `allowed_domains` (alternative)

Use this if Brave is unavailable or you want AI-summarized results alongside URLs:

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "YOUR QUERY HERE"}],
    "tools": [{"type": "web_search", "allowed_domains": ["reddit.com"]}]
  }' | jq -r '.output[] | select(.type == "message") | .content[] | .annotations[]? | .url' | sort -u
```

## Step 2 — Extraction

### Reddit JSON API

Append `.json` to any Reddit post URL to get the full post and all comments as JSON. No authentication required — the API is public.

```bash
curl -s 'https://www.reddit.com/r/SUBREDDIT/comments/POST_ID/TITLE/.json' \
  -H 'User-Agent: gtm-agent/1.0' \
  | jq '{
    post: .[0].data.children[0].data | {title, author, score, selftext, num_comments, subreddit},
    comments: [.[1].data.children[] | select(.kind == "t1") | {
      author: .data.author,
      score: .data.score,
      body: .data.body
    }]
  }'
```

**Post data** is at `.[0].data.children[0].data`. Key fields: `title`, `selftext`, `score`, `num_comments`, `author`, `subreddit`.

**Comments** are at `.[1].data.children[]` where `kind == "t1"`. Key fields: `.data.author`, `.data.body`, `.data.score`.

## Subreddit Search

Reddit's own search endpoint also returns JSON — useful when you already know which subreddit to target:

```bash
curl -s 'https://www.reddit.com/r/{subreddit}/search.json?q={query}&sort=relevance&limit=25' \
  -H 'User-Agent: gtm-agent/1.0' \
  | jq '.data.children[] | {title: .data.title, url: ("https://www.reddit.com" + .data.permalink), score: .data.score, num_comments: .data.num_comments}'
```

## Full Pipeline Example

End-to-end: Brave Search discovers posts, then each post is fetched in full:

```bash
# Step 1: Find relevant posts
URLS=$(curl -s "https://api.search.brave.com/res/v1/web/search?q=site%3Areddit.com+YOUR+QUERY&count=5" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  | jq -r '.web.results[].url')

# Step 2: Extract full content from each post
for url in $URLS; do
  echo "=== $url ==="
  curl -s "${url}.json" -H 'User-Agent: gtm-agent/1.0' \
    | jq '{
      post: .[0].data.children[0].data | {title, author, selftext, score},
      comments: [.[1].data.children[] | select(.kind == "t1") | {author: .data.author, body: .data.body, score: .data.score}][0:5]
    }'
  sleep 1  # Rate limit courtesy
done
```

## Tips

- **Rate limiting** — Reddit returns 429 if you hit it too fast. Add a 1-second delay between requests (`sleep 1`).
- **User-Agent is required** — Reddit rejects requests without a `User-Agent` header with a 429 (or empty response). Always pass `-H 'User-Agent: gtm-agent/1.0'`.
- **`.json` works broadly** — appending `.json` works on post URLs, subreddit listing URLs, search URLs, and comment permalink URLs.
- **URL-encode search queries** — spaces and special characters in Brave Search query strings must be URL-encoded (e.g., `%20` for space, `%3A` for `:`).

## Common Mistakes

- **Forgetting the User-Agent header** — Reddit returns 429 or an empty response without it. Always include `-H 'User-Agent: gtm-agent/1.0'`.
- **Not URL-encoding search queries** — unencoded spaces or special characters in the Brave Search `q` parameter will return no results or an error.
- **Using trafilatura or web_fetch on `.json` URLs** — these tools return mangled output when pointed at JSON endpoints. Use `curl` + `jq` directly.
- **Appending `.json` after query parameters** — `.json` must come before any `?`. Correct: `https://www.reddit.com/r/sub/comments/abc/title/.json`. Wrong: `https://www.reddit.com/r/sub/comments/abc/title/?ref=search.json`.
