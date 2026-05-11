---
name: yfinance
description: "Use when fetching equity data from Yahoo Finance — fundamentals, prices, valuation multiples, peers, options, dividends, analyst estimates. Trigger phrases: 'current price of X', 'P/E for X', 'revenue/earnings of X', 'how does X compare to peers', 'price history of X'."
---

# Yahoo Finance (yfinance)

Fetch equity data via the `yfinance` Python package. No API key required, but Yahoo
throttles aggressively — keep requests focused and cache results inside a session.

The driver is `yfin.py` at the skill root. Every subcommand prints JSON to stdout,
errors print `{"error": "...", "hint": "..."}` and exit 1, usage errors exit 2.

## Workflow

1. Pick the **smallest** subcommand that answers the question. Don't fetch full
   financials when the user asked for a single multiple.
2. Run via the bash tool: `python skills/yfinance/yfin.py <subcommand> <ticker> [opts]`.
3. Read the JSON, surface the answer with the **as-of** date alongside any number.

## Subcommands

| Command | What it returns | Use when |
|---|---|---|
| `profile <ticker>` | name, sector, industry, market cap, employees, country, currency | "what is X", quick context, sector lookup |
| `quote <ticker>` | last price, day change, day range, volume, currency | "current price of X", "how is X trading" |
| `multiples <ticker>` | trailing/forward P/E, EV/EBITDA, P/S, P/B, PEG, market cap, EV | valuation questions, comparing to peers |
| `financials <ticker> [--period annual\|quarterly]` | income, balance sheet, cash flow (last 4 periods) | revenue, earnings, margins, leverage, FCF |
| `prices <ticker> [--period 1y\|5y\|max] [--interval 1d\|1wk\|1mo]` | OHLCV time series | price history, drawdowns, return calc |
| `dividends <ticker>` | dividend history with dates | dividend yield, payout history |
| `splits <ticker>` | split history | adjusting historical prices |
| `peers <ticker>` | sector peers list (best-effort) | comparable companies for analysis |
| `options <ticker> [--expiry YYYY-MM-DD]` | expiries list, or chain for an expiry | implied vol, options pricing |
| `analyst <ticker>` | recommendations, price targets, EPS estimates | sell-side consensus |
| `info <ticker>` | full Yahoo info dict | escape hatch when nothing else fits |

## Examples

**Quick lookup — current P/E:**
```bash
python skills/yfinance/yfin.py multiples LLY
```
Returns:
```json
{
  "ticker": "LLY",
  "as_of": "2026-05-10",
  "trailing_pe": 65.2,
  "forward_pe": 38.7,
  "ev_ebitda": 47.1,
  "ps": 18.4,
  "pb": 62.0,
  "peg": 1.4,
  "market_cap_usd": 740000000000,
  "enterprise_value_usd": 752000000000
}
```

**Revenue & margins:**
```bash
python skills/yfinance/yfin.py financials LLY --period annual
```
Returns the last 4 annual income statements (revenue, gross profit, operating income, net income) plus key balance sheet and cash flow lines.

**Peers for a comparable analysis:**
```bash
python skills/yfinance/yfin.py peers LLY
```

**5-year price history at weekly resolution:**
```bash
python skills/yfinance/yfin.py prices LLY --period 5y --interval 1wk
```

## Reference

### Common tickers

US equities use the bare ticker (`LLY`, `AAPL`). Non-US use exchange suffixes:
`.TO` (Toronto), `.L` (London), `.HK` (Hong Kong), `.T` (Tokyo). ETFs use
the bare ticker (`SPY`, `QQQ`).

### Numbers are floats — preserve precision

The driver returns raw floats from yfinance. Don't pre-round before passing
to other tools or the user; round only at presentation.

### `as_of` is always present

Every subcommand stamps an `as_of` ISO date so the agent can cite freshness.
For real-time fields like `quote.price` this is the trade timestamp.

### Currency

`profile.currency` and `financials.currency` reflect the company's reporting
currency, not necessarily USD. Multi-currency comparisons need explicit FX
conversion (use the `fred` skill for FX rates if needed).

## Common mistakes

- **Calling `info` first by reflex.** It returns 200+ fields and floods the
  context. Start with `profile` / `multiples` / `quote` and only escalate.
- **Forgetting the `as_of` date in your answer.** Every quoted number needs
  a date — fundamentals lag earnings releases by days to weeks.
- **Treating yfinance as authoritative.** It scrapes Yahoo, which sometimes
  has stale or wrong values. For load-bearing fundamentals (e.g. drug-revenue
  scenarios), cross-check against `sec-edgar`'s 10-K facts.
- **Hammering on rate limits.** If you get an empty result, wait 5-10s before
  retrying; if it keeps failing, fall back to `sec-edgar` for fundamentals.
