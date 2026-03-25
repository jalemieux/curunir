---
name: gemini-search
description: "Use when you need grounded web search or YouTube summarization via the Gemini API. Trigger: a skill or task requires web research with source citations through Google Search, or needs to summarize a YouTube video."
---

# Gemini Search

Grounded web search and YouTube summarization via the Gemini REST API. Requires `GEMINI_API_KEY` env var and `curl`/`jq`.

## Usage

**Endpoint:** `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=$GEMINI_API_KEY`

### Grounded Web Search

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "YOUR QUERY HERE"}]}],
    "tools": [{"google_search": {}}]
  }' | jq .
```

### YouTube Summarization

Pass the YouTube URL in the query — grounded search handles the rest:

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Summarize this YouTube video: https://www.youtube.com/watch?v=VIDEO_ID"}]}],
    "tools": [{"google_search": {}}]
  }' | jq .
```

### Extracting Text + Sources

```bash
# Text content only
... | jq -r '.candidates[0].content.parts[0].text'

# Source URLs with titles
... | jq -r '.candidates[0].groundingMetadata.groundingChunks[]? | "- [\(.web.title)](\(.web.uri))"'

# Both together
... | jq -r '{text: .candidates[0].content.parts[0].text, sources: [.candidates[0].groundingMetadata.groundingChunks[]?.web | {title, uri}]}'
```

## Models

| Model | Use when |
|-------|----------|
| `gemini-2.5-flash` | Default — fast, cheap, good for most search queries |
| `gemini-2.5-pro` | Complex multi-step research needing deeper reasoning |

## Examples

**Market research with grounded sources:**

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "What are the top vocabulary learning apps in 2026? Compare pricing and key features. Include sources."}]}],
    "tools": [{"google_search": {}}]
  }' | jq -r '{text: .candidates[0].content.parts[0].text, sources: [.candidates[0].groundingMetadata.groundingChunks[]?.web | {title, uri}]}'
```

**Competitor pricing research:**

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "What is Grammarly Premium pricing in 2026? Include any recent price changes or new tiers."}]}],
    "tools": [{"google_search": {}}]
  }' | jq -r '.candidates[0].content.parts[0].text'
```

**YouTube video analysis for GTM research:**

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Summarize this product demo video. Extract: key features shown, target audience signals, and pricing mentioned. https://www.youtube.com/watch?v=VIDEO_ID"}]}],
    "tools": [{"google_search": {}}]
  }' | jq -r '.candidates[0].content.parts[0].text'
```

## Tips

- The model performs its own follow-up searches — one well-phrased query often beats multiple narrow ones.
- Ask for sources in the prompt text ("Include sources") to get the model to weave citations into its response. The `groundingMetadata` always includes raw source URLs regardless.
- For GTM research, pair with `xai-search` skill: use `gemini-search` for web research (review sites, competitor pages, market data) and `xai-search` for X/Twitter social listening.
- The `google_search` tool is always `{}` — no parameters needed. Google Search grounding handles query formulation automatically.

## Common Mistakes

- **Using the old `@google/generative-ai` SDK** — that package is deprecated. This skill uses the REST API directly via curl, matching the xai-search pattern.
- **Using the Gemini CLI (`gemini -p`) instead of the API** — the CLI is a full agent with noisy output. The REST API gives clean JSON with structured grounding metadata.
- **Forgetting to check `GEMINI_API_KEY`** — verify the env var is set before calling. A missing key returns a 403 that's easy to misdiagnose.
- **Parsing the wrong response path** — text is at `.candidates[0].content.parts[0].text`, sources are at `.candidates[0].groundingMetadata.groundingChunks[]`. Not `.output[]` like the xAI API.
- **Using `googleSearch` instead of `google_search`** — the REST API uses snake_case (`google_search`), not camelCase. The SDK uses camelCase but we're calling REST directly.
