# Finance Persona

Local, private personal-finance assistant. Activated with
`CURUNIR_PERSONA=finance`.

## What it curates

- **Skills** — see `persona.yaml` `skills:` (analysis, memos, FRED, EDGAR,
  yfinance). `thesis-management` and `position-tracking` are added when
  PR #277 ships them.
- **Tools** — the standard default tool set (see `persona.yaml` `tools:`).
- **Prompt** — `expertise/10-domain.md` (focus areas) and
  `expertise/20-guardrails.md` (no regulated advice, privacy), layered on top
  of `context/identity.md` + `context/behavior.md`.

## Required keys

| Key | Used by | Notes |
|-----|---------|-------|
| `FRED_API_KEY` | `fred` skill | Free key from https://fred.stlouisfed.org/docs/api/api_key.html |

The model API key depends on your `MODEL`/`API_BASE`. The default config
points at a local Ollama (no third-party key needed).

## First boot

```bash
cp personas/finance/.env.finance.example .env   # fill in keys
CURUNIR_PERSONA=finance python run.py
```

On first run, `expertise/*.md` is copied into `context/persona/` (never
overwriting existing files) so you can tailor it locally.
