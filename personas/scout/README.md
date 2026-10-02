# Scout Persona

A 24/7 headhunter. Scout sources roles, scores each one against the user's
profile, tracks a job pipeline in the CRM store, and drafts outreach the user
approves before anything is sent. Activated with `CURUNIR_PERSONA=scout`, or
as a sibling agent in a container manifest (see `container.example.yaml`).

## What it curates

- **Skills** — see `persona.yaml` `skills:`: sourcing (`web-search`,
  `xai-search` for X job posts, `linkedin-research`, `playwright` for
  JS-rendered career pages), diligence (`deep-research`, `reddit-research`),
  pipeline tracking (`crm`), ingestion (`document-ingest`), outreach copy
  (`humanizer`) and delivery (`email-send`).
- **Prompt** — `prompts/10-domain.md` (the headhunter role: source, match,
  track, draft, cadence) and `prompts/20-guardrails.md` (never send, apply or
  spend without approval; matches are tool-backed; the pipeline lives in the
  store, never in prose), layered on top of the agent's `identity.md`.

The default tool set is unchanged; personas don't curate core tools.

## Pipeline on the CRM store

The `crm` engine's stages are fixed (`new → contacted → qualified → trial →
won / lost`). Scout maps the job pipeline onto them and keeps the finer
job stage in each lead's `extra.job_stage`:

| Job stage | CRM stage |
|-----------|-----------|
| discovered | `new` |
| shortlisted | `qualified` |
| outreach drafted / applied | `contacted` |
| interviewing | `trial` |
| offer | `won` |
| passed / rejected | `lost` |

A configurable stage set in the engine is a follow-up; this mapping is the
v1 contract.

## Required keys

| Key | Used by | Notes |
|-----|---------|-------|
| `BRAVE_API_KEY` | `web-search`, `reddit-research`, `linkedin-research` | Brave Search API key |
| `XAI_API_KEY` | `xai-search`, `reddit-research`, `linkedin-research` | X / Grok search — X job posts |
| `GEMINI_API_KEY` | `linkedin-research` | Gemini grounding for LinkedIn lookups |

`email-send` additionally needs the Fastmail channel configured
(`FASTMAIL_USER` / `FASTMAIL_PASSWORD`) for the weekly review email; without
it Scout reports in chat.

## First boot

As a sibling of the everyday agent:

```bash
cp container.example.yaml container.yaml   # add a `scout` entry
CURUNIR_CONTAINER=container.yaml python run.py
python cli.py --agent scout
```

On the first conversation Scout asks for what it needs to score roles
(target titles, comp floor, location constraint, IC vs. management, stack,
domain) and records it in memory. The resume goes in the shared `uploads/`
or `workspace/` area and Scout ingests it from there.
