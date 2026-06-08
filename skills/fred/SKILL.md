---
name: fred
description: "Use to get hard, authoritative US macroeconomic data — interest rates, treasury yields, inflation (CPI/PCE), GDP, unemployment, sector indices, FX rates. Reach for this instead of answering macro questions from memory; the figure must come from the live source. Trigger phrases: '10y treasury', 'fed funds rate', 'CPI', 'unemployment rate', 'discount rate inputs', 'macro context'."
---

# US macroeconomic data (via FRED)

Use this skill whenever you need a **hard, sourced US macro figure** — rates, yields,
inflation, GDP, unemployment, FX — never answer from memory or training data. The data
comes from FRED (Federal Reserve Economic Data, St. Louis Fed) — the implementation and
the authoritative source. Every figure carries an observation date so you can cite it.

Requires `FRED_API_KEY` in `.env` (free at https://fred.stlouisfed.org/docs/api/api_key.html).

The driver is `fred.py` at the skill root. Every subcommand prints JSON to stdout,
errors print `{"error": "...", "hint": "..."}` and exit 1, usage errors exit 2.

## Workflow

1. If you don't know the series ID, run `search` first with a short keyword.
2. Use `latest` for a single number ("what is X right now"). Use `series` when
   you need history.
3. Always cite the series ID and observation date in your answer.

## Subcommands

| Command | What it returns | Use when |
|---|---|---|
| `latest <id>` | most recent observation (date, value) + series title | "current 10y treasury yield" |
| `series <id> [--start YYYY-MM-DD] [--end ...] [--limit N]` | observations as `[{date, value}, ...]` | charts, historical context, scenario inputs |
| `search <query> [--limit N]` | matching series with id, title, frequency, units | "find the index for healthcare PPI" |
| `info <id>` | metadata for a series (units, frequency, last update) | confirming a series before pulling history |

## Most-used series IDs

These come up constantly — keep them in mind so you don't always have to `search`.

| ID | What it is | Frequency |
|---|---|---|
| `DGS10` | 10-year Treasury constant maturity yield | daily |
| `DGS2` | 2-year Treasury yield | daily |
| `DGS30` | 30-year Treasury yield | daily |
| `DFF` | Effective Federal Funds Rate | daily |
| `FEDFUNDS` | Federal Funds Rate (monthly average) | monthly |
| `CPIAUCSL` | CPI All Urban Consumers (SA) | monthly |
| `CPILFESL` | Core CPI (excl. food & energy) | monthly |
| `PCEPI` | PCE Price Index | monthly |
| `UNRATE` | Unemployment Rate | monthly |
| `GDP` | Nominal GDP | quarterly |
| `GDPC1` | Real GDP (chained 2017 \$) | quarterly |
| `T10Y2Y` | 10y–2y Treasury spread (recession indicator) | daily |
| `DEXUSEU` | USD/EUR exchange rate | daily |
| `DEXJPUS` | JPY/USD exchange rate | daily |
| `DCOILWTICO` | WTI crude oil spot | daily |
| `VIXCLS` | VIX | daily |

## Examples

**Current 10y Treasury yield (for a discount rate):**
```bash
python skills/fred/fred.py latest DGS10
```

**5-year history of Core CPI YoY:**
```bash
python skills/fred/fred.py series CPILFESL --start 2021-01-01
```

**Find a healthcare-specific inflation index:**
```bash
python skills/fred/fred.py search "healthcare prices" --limit 10
```

## Reference

### Setup

Set `FRED_API_KEY` in `.env`. Get one free at:
https://fred.stlouisfed.org/docs/api/api_key.html

### Output shape

`latest` and `series` always include the series `title` and `units` so the
agent can present the number with the right label. `value` is `null` when
FRED reports a missing observation (denoted `.` in their raw API).

```json
{
  "id": "DGS10",
  "title": "Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity",
  "units": "Percent",
  "frequency": "Daily",
  "as_of": "2026-05-09",
  "value": 4.27
}
```

### Frequency mismatches

When mixing series in one analysis (e.g., daily DGS10 + monthly CPI), be
explicit about the alignment in your answer — don't silently treat a monthly
average as a daily reading.

## Common mistakes

- **Searching for the right ID is fine, but don't paginate forever** —
  3-5 search results is plenty. If the top hits aren't right, refine the
  query rather than going deeper.
- **Quoting a stale `latest`** — daily series can be 1-2 business days
  behind; monthly series can be 30+ days behind. Always cite the observation
  date.
- **Treating CPI level as inflation** — `CPIAUCSL` is the index level. To
  report YoY inflation, divide latest by value 12 months earlier. Or use
  `CPIAUCSL_PC1` (year-over-year % change) directly.
- **Hitting rate limits** — FRED allows 120 req/min. Batch what you need;
  don't fetch the same series twice in one session.
