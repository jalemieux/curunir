# Sensitivity Analysis

Sensitivity answers: **which assumptions actually move the answer, and
which are noise?** A reader who knows the implied price ranges from $X to
$Y based on a ±20% change in incremental revenue understands the analysis
better than one who just sees the base case.

## The discipline

1. **Identify the 1-2 assumptions that drive the result most.** Usually
   these surface naturally from the scenario work — they're the things
   that are different between bull and bear.
2. **Vary each one ±20% (or ±some-defensible-band) holding everything
   else constant.** Re-run the implied-price calculation for each step.
3. **Show the result as a small table or band.** The point is the *shape*
   of the sensitivity, not the precise numbers.

Don't sensitivity-test 8 variables. Two well-chosen ones tell the story.

## Format

For each driver, a small table:

```markdown
**Sensitivity: Incremental drug revenue (Base case ± bands)**
| Incr. Revenue | EPS | Implied Price (Peer P/E) | vs Current |
|---|---|---|---|
| -20% ($24B) | $X | $Y | +Z% |
| Base ($30B) | $X | $Y | +Z% |
| +20% ($36B) | $X | $Y | +Z% |
```

If you want to show how two drivers interact, a 3×3 grid works:

```markdown
**Sensitivity: Incremental revenue × Net margin (implied price, peer P/E)**
| | Margin -200bps | Margin (base) | Margin +200bps |
|---|---|---|---|
| Revenue -20% | $A | $B | $C |
| Revenue (base) | $D | $E | $F |
| Revenue +20% | $G | $H | $I |
```

Don't go bigger than 3×3. Larger grids look quantitative but communicate
less.

## Choosing bands defensibly

- **±20%** is a fine default for revenue or volume drivers. It's enough
  to surface real swings without implying false precision.
- **±50bps to ±200bps** for margin assumptions, depending on the
  industry's typical margin volatility.
- **±100bps to ±200bps** for discount-rate / WACC inputs (we don't do
  full DCF here, but if you cite a discount rate anywhere, sensitize it
  too).

If you use a non-default band, justify it in the assumptions block —
"using ±10% on revenue because guidance ranges this tightly" or similar.

## The interpretation paragraph

After the table, write 1-2 sentences that translate. Examples:

> Implied price is more sensitive to net margin than to incremental
> revenue: a 200bp margin compression cuts the peer-P/E target by ~18%,
> while a 20% revenue shortfall cuts it by ~12%. This argues for watching
> operating leverage commentary on the next earnings call more closely
> than the headline revenue beat/miss.

That's the kind of sentence that makes the analysis useful — it points
the reader at what to monitor.

## Common mistakes

- **Sensitizing every input.** Pick the 1-2 that matter. Listing the
  sensitivity of every line item buries the signal.
- **Confusing absolute and relative bands.** "±20%" on revenue means
  ±20% of revenue, not ±20 absolute dollars. State the absolute numbers
  in the table so the reader doesn't have to do the math.
- **Implying the band is a confidence interval.** It isn't — it's a
  what-if. Say "if X moves by ±20%, the answer moves by ±Y%", not "we
  estimate X within ±20%".
