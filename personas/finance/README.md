# Finance Persona

Local, private personal-finance assistant. Activated with
`CURUNIR_PERSONA=finance`.

## What it curates

- **Skills** — see `persona.yaml` `skills:` (analysis, memos, tax-strategy,
  FRED, EDGAR,
  yfinance, plus a research layer: catalyst-memo, deep-research,
  fact-checker, polymarket, digest, reddit-research, youtube-transcript,
  podcast-ingest, and the web-search/xai-search/gemini-search backends).
- **Prompt** — `prompts/10-domain.md` (focus areas) and
  `prompts/20-guardrails.md` (no regulated advice, privacy), layered on top
  of `context/identity.md`.

The default tool set is unchanged; personas don't curate core tools.

## Required keys

| Key | Used by | Notes |
|-----|---------|-------|
| `FRED_API_KEY` | `fred` skill | Free key from https://fred.stlouisfed.org/docs/api/api_key.html |
| `BRAVE_API_KEY` | `web-search`, `reddit-research`, `digest` | Brave Search API key |
| `XAI_API_KEY` | `xai-search`, `reddit-research` | xAI (Grok) API key |
| `GEMINI_API_KEY` | `gemini-search` | Google Gemini API key |

The research skills degrade gracefully if a search backend's key is
missing, but for full coverage all three search backends should be keyed.

The model API key depends on your `MODEL`/`API_BASE`. The default config
points at a local Ollama (no third-party key needed).

## First boot

```bash
cp personas/finance/.env.finance.example .env   # fill in keys
CURUNIR_PERSONA=finance python run.py
```
