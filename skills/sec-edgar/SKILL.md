---
name: sec-edgar
description: "Use when fetching official SEC filings or standardized fundamentals (10-K, 10-Q, 8-K) for US-listed companies — segment revenue, guidance, drug pipelines, risk factors, executive comp, beneficial ownership. Trigger phrases: 'what does X's 10-K say', 'segment revenue', 'authoritative revenue numbers', 'check the filing', 'recent 8-K'."
portal_summary: "Pull official SEC filings — 10-K, 10-Q, 8-K — for a US-listed company"
---

# SEC EDGAR

Fetch official SEC filings and standardized historical fundamentals via the
EDGAR API. No API key required, but SEC requires every request to include a
`User-Agent` header that identifies the caller (set `SEC_USER_AGENT` in `.env`,
e.g. `curunir/0.1 (your-email@example.com)`).

The driver is `edgar.py` at the skill root. Every subcommand prints JSON to stdout,
errors print `{"error": "...", "hint": "..."}` and exit 1, usage errors exit 2.

## Workflow

1. **Resolve the ticker** to a CIK (`lookup`). Cache the CIK mentally —
   subsequent commands accept either a ticker or a CIK.
2. **For numbers** (revenue, EPS history, R&D spend, segment data): use
   `facts` — it returns standardized XBRL data normalized across years.
3. **For text** (risk factors, MD&A, drug pipeline language, guidance):
   use `filings` to find the right accession number, then `fetch` to pull
   the document content.
4. Cite the filing date and accession number alongside any quoted text.

## Subcommands

| Command | What it returns | Use when |
|---|---|---|
| `lookup <ticker>` | CIK + canonical company name | start of any filing-driven analysis |
| `facts <ticker> [--concept Revenues]` | standardized historical XBRL facts (revenue, EPS, R&D, etc.) | "actual reported revenue 2020-2024", "growth in R&D spend" |
| `filings <ticker> [--type 10-K] [--limit 10]` | recent filings index (form, date, accession, primary doc URL) | "most recent 10-K", "8-Ks since the drug approval" |
| `fetch <accession>` | filing primary document text (HTML stripped) | reading risk factors, MD&A, drug pipeline disclosures |

## Examples

**Resolve a ticker to a CIK:**
```bash
python skills/sec-edgar/edgar.py lookup LLY
# {"ticker": "LLY", "cik": "0000059478", "name": "ELI LILLY & Co"}
```

**Standardized historical revenue (a single concept):**
```bash
python skills/sec-edgar/edgar.py facts LLY --concept Revenues
```

**Last 5 10-K filings:**
```bash
python skills/sec-edgar/edgar.py filings LLY --type 10-K --limit 5
```

**Read the most recent 10-K text:**
```bash
ACC=$(python skills/sec-edgar/edgar.py filings LLY --type 10-K --limit 1 \
      | jq -r '.filings[0].accession')
python skills/sec-edgar/edgar.py fetch "$ACC"
```

## Reference

### Setup

Set `SEC_USER_AGENT` in `.env`. Format: `<app>/<version> (<contact-email>)`.
SEC will reject requests without it.

### Common XBRL concepts

`facts --concept` accepts standard US-GAAP taxonomy names:

| Concept | What it is |
|---|---|
| `Revenues` | Total revenue (older filings) |
| `RevenueFromContractWithCustomerExcludingAssessedTax` | Total revenue (post-ASC 606) |
| `NetIncomeLoss` | Net income |
| `EarningsPerShareBasic` / `EarningsPerShareDiluted` | EPS |
| `ResearchAndDevelopmentExpense` | R&D spend |
| `Assets` / `Liabilities` / `StockholdersEquity` | balance sheet totals |
| `CashAndCashEquivalentsAtCarryingValue` | cash on hand |
| `OperatingIncomeLoss` | operating income |
| `CostOfRevenue` | COGS |

If you don't pass `--concept`, `facts` returns the full company-facts blob
(can be large — only do this if you need to discover what's available).

### Form types

- `10-K` — annual report (full year)
- `10-Q` — quarterly report
- `8-K` — material events (drug approvals, M&A, exec changes)
- `DEF 14A` — proxy statement (exec comp)
- `13F-HR` — institutional holdings
- `4` — insider transactions

Pass any form type literal to `--type`. EDGAR also accepts amendments
(`10-K/A`) — those show up automatically.

### `fetch` output is text-extracted

`fetch` strips HTML and returns plain text from the filing's primary document.
For full structured access (exhibits, schedules), use the URLs in
`filings` and pull them with curl/web_fetch.

## Common mistakes

- **No User-Agent** — SEC returns 403. Make sure `SEC_USER_AGENT` is set.
- **Conflating XBRL concept names** — pre-ASC 606 filings used `Revenues`;
  post-2018 use `RevenueFromContractWithCustomerExcludingAssessedTax`. The
  `facts` driver returns whichever exists; for very long histories you may
  need both and stitch.
- **Reading the entire 10-K when you want one section** — 10-Ks are 100-300
  pages of text. After `fetch`, use grep/search on the returned text rather
  than dumping it all into context.
- **Trusting EDGAR for prices** — EDGAR is filings, not market data.
  Use `yfinance` for prices and multiples.
