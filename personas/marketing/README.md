# Marketing Persona

Go-to-market assistant for builders. Activated with
`CURUNIR_PERSONA=marketing`.

Takes a product from "just shipped" to a repeatable way to reach buyers —
the work a sharp fractional CMO does: deep product understanding, ICP and
positioning, an actionable GTM plan, competitive intelligence, and demand
validation.

## What it curates

- **GTM pipeline** — `gtm-onboard-ingest` (product context) →
  `gtm-position-segment` (ICPs, messaging, pricing) → `gtm-plan` (per-ICP
  execution plan). The phases build on each other; respect the order.
- **Competitive intelligence** — `gtm-competitive-landscape` (full landscape
  + incumbent feature watch), `gtm-competitive-monitor` (periodic delta scan,
  schedulable), `gtm-reassess` (fold new intel back into existing GTM docs).
- **Demand validation** — `gtm-smoke-test` (fake-door tests that measure
  willingness to transact, for ideas without a product yet).
- **Research stack the pipeline depends on** — `web-search`,
  `xai-search`, `gemini-search`, `reddit-research`, `linkedin-research`,
  `playwright`, plus `humanizer` (de-AI listing/outreach copy).
- **Prompt** — `prompts/10-domain.md` (GTM focus areas, phase ordering) and
  `prompts/20-guardrails.md` (builder owns decisions, no fabricated signal,
  honest smoke tests, privacy), layered on top of `context/identity.md`.

The default tool set is unchanged; personas don't curate core tools.

## Required keys

| Key | Used by | Notes |
|-----|---------|-------|
| `BRAVE_API_KEY` | `web-search`, `reddit-research`, `linkedin-research` | Brave Search API key |
| `XAI_API_KEY` | `xai-search`, `reddit-research` | xAI (Grok) API key — X/Twitter social listening |
| `GEMINI_API_KEY` | `gemini-search` | Google Gemini API key — grounded search + YouTube |

Every GTM skill degrades gracefully when a research backend's key is missing
(it falls back or skips that layer and warns the builder), but for full
coverage all three backends should be keyed.

The model API key depends on your `MODEL`/`API_BASE`. The default config
points at a local Ollama (no third-party key needed).

## First boot

```bash
cp personas/marketing/.env.marketing.example .env   # fill in keys
CURUNIR_PERSONA=marketing python run.py
```
