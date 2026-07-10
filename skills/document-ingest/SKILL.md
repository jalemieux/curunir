---
name: document-ingest
description: "Use when a large document (report, filing, contract, statement) needs a document card — a compact navigation map (type, entities, key figures with line refs, section map) so questions can be answered via targeted reads instead of loading the whole text. Run `python skills/document-ingest/ingest.py <path>` via bash; it performs one tool-less LLM pass over the full document, writes `<path>.card.md` next to it, and prints the card. Re-running is free: an existing card is reused. Trigger: a read was gated as too large, an attachment manifest lists a big document, or the user asks to 'ingest'/'index'/'card' a document. Do NOT use for small files — read those directly."
---

# Document card: <filename>

- **Type:** <what kind of document, and its period/date if any — e.g. "Q3 2025 10-Q, fiscal year ending Sep">
- **Entities:** <organizations, people, accounts, tickers the document is about>

## Key figures
<the handful of facts and figures someone is most likely to ask about — one
per line, each ending with its location, e.g.:>
- Net revenue FY2025: $4.2B — lines 812-815
- Total long-term debt: $1.1B — lines 1204-1210

## Section map
<one line per major section of the document:>
- <section name> — lines N-M

## Gist
<2-4 sentence neutral summary of what the document covers — what it is, not
what to conclude from it>
