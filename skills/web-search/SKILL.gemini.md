---
name: web-search
description: "Search the web using Gemini grounded search (Google) via bash — returns a synthesized answer with source citations"
---

# Web Search (Gemini-backed A/B arm)

> **This is the experimental Gemini-backed variant of `web-search`, staged for an A/B against the Brave version.** It keeps the skill name `web-search` so routing and the eval's `load_skill: web-search` check are unchanged; only the backend differs. Swap it in with the commands in the A/B section of your notes, run the suite, then revert.

Use Gemini's grounded search (Google Search tool) via `curl`. The API key is in `GEMINI_API_KEY`. Unlike a raw search index, this returns a **synthesized answer plus citations** — Google's Maps/Places data is grounded in, so ratings, hours, and addresses come back as real cited data rather than needing a separate fetch.

## Basic Search

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "YOUR QUERY HERE — ask for sources"}]}],
    "tools": [{"google_search": {}}]
  }' | jq -r '{text: .candidates[0].content.parts[0].text, sources: [.candidates[0].groundingMetadata.groundingChunks[]?.web | {title, uri}]}'
```

- `.candidates[0].content.parts[0].text` — the synthesized answer.
- `.candidates[0].groundingMetadata.groundingChunks[]?.web` — the cited sources (`title`, `uri`).
- Use `gemini-2.5-flash` by default; `gemini-2.5-pro` for deeper multi-step research.

## Local businesses & "well-reviewed near me" queries

This is where the Gemini backend shines: Google grounds in Maps/Places, so a query like *"well-reviewed coffee shops near Union Square SF, with ratings and hours"* returns named shops **with real ratings, review counts, addresses, and hours** as grounded, cited data.

1. **Ask for the specifics you want** — "name 2-3 well-reviewed <X> near <place>; include address, rating, and hours, with sources."
2. **Report only what the grounded answer + citations actually contain.** The grounding gives you real data — use it. But if a specific isn't in the response, don't invent it; say so and offer to check the business's own site.
3. **Never `web_fetch` the aggregator pages** (Yelp/Google/Reddit results) — you don't need to; the grounded answer already carries the data, and those pages are bot-blocked anyway.

## Guardrail — report only what the source returned

The no-fabrication rule is unchanged from the Brave arm: never state a rating, review count, phone number, or "as of <date>" freshness claim that isn't in the grounded response or a citation. A grounded synthesis is *more* likely to carry these honestly — but it can still blend, so treat a specific with no matching citation as unverified.

## Reading full pages

To read a specific page's full content, still use `web_fetch` (converts HTML to clean markdown with a targeted `prompt`), and fall back to the `playwright` skill for JS-rendered pages. curl is for the Gemini API (JSON), not for reading page HTML.

## Tips

- Always use `$GEMINI_API_KEY` — never hardcode the token.
- Ask for sources explicitly in the query so `groundingChunks` is populated.
- For deep multi-source research where you want to control which pages enter context, the grounded synthesis is less transparent than search-then-fetch — note that when comparing arms.
