---
name: polymarket
description: "Use when fetching live prediction-market data for forward-looking topics — elections, geopolitical events, policy decisions, macro outcomes, sports, awards, anything where a real-money betting market expresses an implied probability. Trigger phrases: 'prediction markets', 'Polymarket', 'Polymarket odds', 'implied probability', 'betting markets say', 'what are the odds of'. Also cite alongside qualitative sources inside deep-research / deep-research-guided when the question is forward-looking."
portal_summary: "Look up live Polymarket odds and implied probabilities"
---

# Polymarket

Fetch live prediction-market questions, outcomes, and implied probabilities
from Polymarket's public Gamma API. No API key required.

The driver is `polymarket.py` at the skill root. Every subcommand prints JSON
to stdout; errors print `{"error": "...", "hint": "..."}` and exit 1; usage
errors exit 2.

## When to use

- Standalone questions: "what are prediction markets saying about X?",
  "Polymarket odds on Y", "implied probability of Z".
- As one source inside `deep-research` / `deep-research-guided` whenever the
  question is forward-looking (election, conflict, policy outcome, sports,
  awards, macro print). Implied probabilities are a quantitative complement
  to qualitative sources — cite both.

Use prediction-market data as **priced probabilities**, not opinions. Always
quote the market URL, the implied probability, and the `fetched_at`
timestamp so readers know the snapshot can move minute-by-minute.

## Workflow

1. Start with `search <query>` (or `trending` for a "what's hot" scan) to
   find candidate markets.
2. If the user wants detail on a specific market, run `market <slug>`.
3. Cite the market URL, both outcome prices, and `fetched_at` in your reply.

## Subcommands

| Command | What it returns | Use when |
|---|---|---|
| `search <query> [--limit N] [--active-only]` | top matching markets in normalized form | "what's the market on the next Fed cut?" |
| `market <slug-or-id>` | one market in normalized form | drilling into a specific question after `search` |
| `trending [--limit N]` | top active markets ranked by 24h volume | "what are people betting on right now?" |

`--active-only` on `search` filters out closed and archived markets — use it
unless the user is asking about a resolved historical market.

## Normalized output shape

Every subcommand returns markets in the same shape so the agent and
downstream consumers (e.g. `deep-research`) can summarize uniformly, and so
a future `kalshi` skill can adopt the same fields without churn:

```json
{
  "venue": "polymarket",
  "id": "<gamma market id>",
  "question": "Fed rate cut by June 2026 meeting?",
  "slug": "fed-rate-cut-by-june-2026-meeting",
  "url": "https://polymarket.com/market/fed-rate-cut-by-june-2026-meeting",
  "status": "active",
  "end_date": "2026-06-17",
  "volume_usd": 297033.91,
  "outcomes": [
    {"name": "Yes", "price": 0.0165, "implied_prob": 0.0165},
    {"name": "No",  "price": 0.9835, "implied_prob": 0.9835}
  ],
  "fetched_at": "2026-05-24T21:29:22Z"
}
```

Notes on the fields:

- `status` is one of `active`, `closed`, `archived`, `inactive`.
- `price` and `implied_prob` are equal for Polymarket — its share prices
  are denominated 0-1 and directly readable as implied probability. The
  field is duplicated for forward-compatibility with venues whose price
  ≠ probability.
- `volume_usd` is lifetime traded volume in USD (Polymarket settles in
  USDC, 1:1 with USD).
- `end_date` is the market's resolution date (`YYYY-MM-DD`).

## Examples

**What do markets say about a near-term Fed rate cut?**

```bash
python skills/polymarket/polymarket.py search "fed rate cut" --limit 5 --active-only
```

**Drill into a specific market by slug:**

```bash
python skills/polymarket/polymarket.py market fed-rate-cut-by-june-2026-meeting
```

**What's hot right now?**

```bash
python skills/polymarket/polymarket.py trending --limit 10
```

## Reference

### Setup

No key required. Polymarket's Gamma API
(`https://gamma-api.polymarket.com`) is open. Make sure outbound HTTPS to
that host works.

### Citing in reports

When you put a Polymarket number in a report, the citation should carry:

- The market question (verbatim)
- The implied probability (or both outcome prices for binary)
- The market URL
- The `fetched_at` timestamp — prediction-market prices move minute-by-minute

Example: "Polymarket implies a 9.5% probability of a US–Iran permanent peace
deal by May 26, 2026 (Yes 0.095 / No 0.905, [market](https://polymarket.com/market/us-x-iran-permanent-peace-deal-by-may-26-2026), as of 2026-05-24T21:29Z)."

## Common mistakes

- **Quoting a stale price without the timestamp.** Polymarket prices shift
  continuously; a quote without `fetched_at` is unfalsifiable. Always cite
  the snapshot time.
- **Treating Polymarket odds as forecasts of truth.** They are priced
  *probabilities* derived from real-money bets — informative but not
  oracular. Pair them with qualitative sources for context, especially on
  thinly-traded markets (low `volume_usd`).
- **Cherry-picking one market when several exist.** Searches often return
  multiple related markets ("by June meeting", "by September meeting", "by
  year-end"). Pick the one whose resolution criteria actually match the
  user's question, or summarize across several.
- **Searching forever for the perfect match.** The driver returns the API's
  top hits unmodified. If the top 5-10 don't include what you want, refine
  the query rather than paging deeper.
- **Using `trending` for a topical question.** `trending` is a "what's
  popular today" view, not topical. For a specific question, always start
  with `search`.
