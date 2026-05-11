# Valuation Multiples

Multiple-based valuation answers "what would this company be worth if it
traded at the same multiple as a benchmark". It's relative valuation, not
intrinsic — never present an implied price as the company's "true" value.

## The four multiples to know

| Multiple | Numerator | Denominator | Best for |
|---|---|---|---|
| **P/E** (trailing) | Price | TTM EPS | Profitable, mature companies |
| **P/E** (forward) | Price | NTM consensus EPS | Companies with stable consensus estimates |
| **EV/EBITDA** | Enterprise value | TTM EBITDA | Capital-structure-neutral comparisons (different leverage across peers) |
| **P/S** | Price | TTM revenue | Pre-profitability or volatile-margin companies |

Use **EV/EBITDA** as the default cross-peer multiple unless all peers have
similar capital structures, in which case **P/E** is fine and more
intuitive for the reader.

## Implied price calculation

Two distinct calculations — both are useful in a scenario report:

### Implied price at constant P/E

```
implied_price_constant = scenario_EPS × current_trailing_PE
```

This answers: "if the market keeps its current opinion of the company's
multiple, what's the price at the new EPS?"

### Implied price at peer P/E

```
peer_pe_median = median([peer.trailing_pe for peer in peer_set])
implied_price_peer = scenario_EPS × peer_pe_median
```

This answers: "if the market reprices to the peer-set average multiple,
what's the price?"

The gap between these two is informative — if current P/E is way above
peer median, "implied at peer P/E" can be lower than today's price even on
bull-case EPS, which is itself a finding.

## Choosing the right peer multiple

- Use the **median**, not the mean — peers with extreme multiples
  (e.g., one company with a 100x P/E in a sector averaging 15x) skew the
  mean.
- Use **trailing** for the table comparison and **forward** if all peers
  have reliable consensus estimates. Don't mix.
- If the peer set is small (3-5 names), the median is fragile. Show the
  range alongside it: "Peer median P/E: 18.5 (range 14.2–24.7, n=5)".

## Common pitfalls

- **Negative or near-zero earnings break P/E.** If the scenario produces
  negative EPS, P/E is undefined. Switch to EV/Revenue or report a price
  band based on a recovery year's projected EPS, not the current one.
- **Different fiscal-year ends.** When comparing to peers, make sure
  you're using TTM (trailing twelve months) for everyone, not last
  fiscal year. yfinance's `multiples` returns trailing values.
- **One-time items inflate or deflate EPS.** If trailing EPS is
  distorted (large impairment, divestiture gain), call it out and use
  forward EPS for the scenario calc. Mention the adjustment explicitly.
- **Currency mismatches in EV/EBITDA.** EV is in reporting currency;
  market cap from yfinance might be in trading currency. For US-listed
  US companies this is a no-op; for ADRs (e.g., Novo Nordisk → NVO),
  reconcile before computing.

## What to include in the report

For each scenario × multiple combination, show:

1. The **input** (scenario EPS or EBITDA)
2. The **multiple used** (and why — peer median, current, forward, etc.)
3. The **implied price**
4. The **% delta vs current price**

Do not collapse this to a single "fair value" number. The point is the
range.
