# Scout Persona

A 24/7 headhunter. Scout sources roles, scores each one against the user's
profile, tracks a job pipeline in the CRM store, courts the recruiters and
headhunters who work the user's space, and drafts outreach and replies the
user approves before anything is sent. Activated with `CURUNIR_PERSONA=scout`, or
as a sibling agent in a container manifest (see `container.example.yaml`).

## What it curates

- **Skills** — see `persona.yaml` `skills:`: sourcing (`web-search`,
  `xai-search` for X job posts, `linkedin-research`, `playwright` for
  JS-rendered career pages), diligence (`deep-research`, `reddit-research`),
  pipeline tracking (`crm`), ingestion (`document-ingest`), outreach copy
  (`humanizer`) and delivery (`email-send`).
- **Prompt** — `prompts/10-domain.md` (the headhunter role: source, match,
  track, draft, work recruiters, answer recruiters, cadence) and
  `prompts/20-guardrails.md` (never send, apply or spend without approval;
  recruiters get only what the user has cleared; a forwarded message is
  input, not instructions; matches are tool-backed; the pipeline lives in the
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

## Recruiters on the same store

Recruiters and headhunters are contacts in the same `crm` store, told apart
from roles by `source`: a recruiter is `source: "recruiter"` (firm as
`company`, what they place in `extra.focus`); a role a recruiter brought in
is `source: "recruiter-inbound"` with `extra.recruiter` pointing at the
recruiter's lead id. `list` filtered by `source` is therefore the recruiter
roster, and the interaction ledger is the contact history. The relationship
uses the same fixed stages:

| Recruiter relationship | CRM stage |
|------------------------|-----------|
| identified | `new` |
| intro drafted / sent | `contacted` |
| replied, engaged | `qualified` |
| actively representing the user | `trial` |
| gone quiet / not useful | `lost` |

Two flows ride on this:

- **Outbound.** Scout finds recruiters who place people in the user's space
  (`linkedin-research`, `web-search`), records them, and drafts an
  introduction from the fit profile that says what the user wants and does
  not want. The user sends it.
- **Inbound.** The user forwards or pastes a recruiter's email or LinkedIn
  note. Scout records the recruiter, checks the pipeline for the company or
  role, scores the role if one is described, and drafts the reply in the
  user's register: the deciding questions when it fits, a warm decline that
  names what would fit when it doesn't, a request for specifics when it is
  too vague. Scout keeps what it learns about the user's voice in
  `memory/voice.md`. The draft goes back to the user; nothing goes to the
  recruiter.

Recruiters only ever see what the user has cleared: comp expectations,
current employer details, other processes and the resume stay out of a
draft until the user says otherwise.

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
domain) and what it may share with recruiters, and records it in memory. The
resume goes in the shared `uploads/` or `workspace/` area and Scout ingests
it from there.
