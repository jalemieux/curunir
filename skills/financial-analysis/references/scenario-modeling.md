# Scenario Modeling

The point of scenarios is to make uncertainty **legible** instead of hiding
it inside a single point estimate. A reader should be able to see the
distribution of outcomes and decide for themselves how to weight the cases.

## Default structure: Base / Bull / Bear

Three cases, always:

| Case | Definition |
|---|---|
| **Base** | The most likely outcome under current information. Roughly aligned with company guidance and consensus estimates. |
| **Bull** | Optimistic but credible — assumes upside drivers materialize (e.g., faster ramp, label expansion, better-than-expected uptake). |
| **Bear** | Pessimistic but credible — assumes headwinds materialize (e.g., slower ramp, competitive entry, pricing pressure). |

Don't go straight to 5 or 7 cases. The marginal case past 3 buys little and
costs reader attention.

## What to vary

Identify **2-3 key drivers** for the question, vary those, hold everything
else constant. Common drivers in equity scenarios:

- Incremental revenue from a new product / drug / segment
- Adoption rate or volume ramp (peak revenue year, share captured)
- Margin expansion or compression (gross / operating / net)
- Capex / R&D intensity
- Share count (buybacks / issuance)

Resist the urge to vary everything at once. Co-varying drivers makes
attribution impossible.

## Mechanics

For each case, compute:

1. **New revenue line** = current TTM revenue + incremental revenue (case-specific)
2. **New net income** = new revenue × current net margin (or case-specific margin if margin is a driver)
3. **New EPS** = new net income / current share count
4. **Implied share price at constant P/E** = new EPS × current trailing P/E
5. **Implied share price at peer P/E** = new EPS × peer-set median P/E
6. **% upside vs current price** for both #4 and #5

Keep the math explicit in the report. Don't paste opaque numbers — show
the input → output chain.

## Presentation in the report

Always a table. Rows are cases, columns are inputs and outputs:

```markdown
| Case | Incr. Revenue | Total Revenue | Net Income | EPS | Implied @ Current P/E | Implied @ Peer P/E | vs Current Price |
|---|---|---|---|---|---|---|---|
| Base | $X | $Y | $Z | $A | $B (+C%) | $D (+E%) | (base = guidance) |
| Bull | $X | $Y | $Z | $A | $B (+C%) | $D (+E%) | upside if ... |
| Bear | $X | $Y | $Z | $A | $B (+C%) | $D (+E%) | downside if ... |
```

Below the table, write 1-2 sentences per case naming the **assumption**
that defines it (the thing the reader has to believe for that case to be
the right one).

## Example: "Lilly with $30B incremental drug revenue"

User-supplied assumption: incremental drug revenue. Cases vary the **ramp
year and peak share**:

- **Base:** $30B incremental, 3-year ramp, current operating margin → new
  EPS, applied to trailing and peer P/E.
- **Bull:** $30B incremental, 2-year ramp, +200bps margin from operating
  leverage.
- **Bear:** $20B incremental (competition takes 33% of TAM), 4-year ramp,
  flat margins.

The user's $30B becomes the **base case input**, not the "answer". The
scenarios surface what happens if reality differs.

## What not to do

- **Don't anchor cases to round-number price targets.** Pick the input
  assumptions; let the price fall out of them. Working backwards from a
  target price is reverse-engineering.
- **Don't claim probabilities you can't defend** ("60% bull / 30% base
  / 10% bear"). Either cite a real probability source (options-implied,
  prediction market) or leave probabilities out and let the reader weight.
- **Don't forget tax.** If you're modeling pretax revenue impact and
  showing EPS, multiply by (1 − effective tax rate) before dividing by
  shares. Use the company's most recent effective tax rate.
