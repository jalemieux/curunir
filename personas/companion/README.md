# Companion Persona

A direct, accountability-focused life coach, therapist-style listener, and
confidant. Conversation-first, with memory-driven continuity that tracks goals
and patterns over time, and able to ground technique suggestions in real
research. Activated with `CURUNIR_PERSONA=companion`.

## What it curates

- **Skills** — see `persona.yaml` `skills:` (`identity`, `onboarding`,
  `dreaming`, plus a research layer: `deep-research` and the `web-search`
  backend for grounding techniques in real sources).
- **Prompt** — `prompts/10-domain.md` (the coach/confidant role, the direct &
  challenging stance, memory continuity) and `prompts/20-guardrails.md` (no
  clinical claims, honest citation, crisis safety, facts-need-grounding),
  layered on top of `context/identity.md`.

The default tool set is unchanged; personas don't curate core tools.

## Required keys

| Key | Used by | Notes |
|-----|---------|-------|
| `BRAVE_API_KEY` | `web-search`, `deep-research` | Brave Search API key — backs the research used to ground techniques |

The research skills degrade gracefully if the search backend's key is missing,
but for grounded technique suggestions `BRAVE_API_KEY` should be set.

The model API key depends on your `MODEL`/`API_BASE`. The default config points
at a local Ollama (no third-party key needed) — fitting for a private confidant.

## First boot

```bash
cp personas/companion/.env.companion.example .env   # fill in keys
CURUNIR_PERSONA=companion python run.py
```
