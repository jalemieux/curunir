---
name: crm
description: "Use to track the marketing persona's leads and sales pipeline — people (contacts/leads), their company, source (beta-signup, referral, manual), pipeline stage, owner, and the interaction history (emails, calls, notes, stage changes). Trigger phrases: 'add this lead', 'new sign-up', 'track this contact', 'move <lead> to <stage>', 'advance <lead>', 'what's in my pipeline', 'show my leads', 'how many leads in <stage>', 'log a call/email with <lead>', 'ingest this beta sign-up', 'who signed up this week', 'pipeline by stage'. This is the marketing book of business — distinct from GTM planning/positioning (strategy) and competitive intel."
tools: crm
portal_summary: "Track your leads and sales pipeline deterministically"
---

# CRM

Track leads and the sales pipeline in a structured store and answer questions
about them. **The engine does every write and every count** — you never track a
lead's stage in prose or hand-count the pipeline.

## Data model

The store (`context/memory/crm.db`, SQLite) holds `leads` and an append-only
`interactions` ledger. Each lead has a `name` (required), and optional `email`,
`company`, `source` (e.g. `beta-signup`, `referral`, `manual`), `stage`,
`owner`, and `note`. A JSON `extra` overflow column holds anything off-schema
(utm tags, lead score, …). Interactions record a lead's history — each has a
`kind` (`email`, `call`, `note`, `meeting`, `stage_change`) and a `body`; they
are append-only and **survive a lead's deletion** (soft reference).

## Pipeline stages

`new → contacted → qualified → trial → won / lost`. A lead defaults to `new`.
Advance a lead with `set_stage` (never by hand-editing) — it both moves the
lead and logs a `stage_change` interaction so the pipeline is auditable.

## How you reach it

Reach the engine through the `crm` tool when it is available to you (call it
with an `action` and an `args` object). Otherwise run the CLI via bash:
`python skills/crm/crm.py <cmd>`. Both front the same engine.

- **Reads:** `list` (optional `stage`/`source`/`owner`), `show` (`id`),
  `pipeline` (counts by stage), `activity` (interaction ledger; optional
  `lead_id`/`since`/`limit`), `query` (read-only SELECT), `render`.
- **Writes:** `add`, `set` (update fields), `set_stage` (`id`, `stage`), `rm`,
  `log` (an interaction: `lead_id`, `kind`, `body`), `import_rows` (bulk load).

Tool actions use the names above. CLI subcommands match but hyphenate
multi-word names — `set-stage`, `import-rows` (the CLI `import-rows` takes
`--rows-file <json>`).

## Ingestion (the driving use case)

Recording a beta sign-up is a single `add` with `source="beta-signup"`:

```
crm add {name, email, company, source: "beta-signup"}
```

The lead lands in stage `new`; advance it through the pipeline with `set_stage`
as it progresses. To bulk-load a sign-up export the user has pasted, map the
rows and call `import_rows` with `source="beta-signup"` rather than many
one-by-one `add` calls.

## Disciplines (non-negotiable)

- **Never track pipeline in prose.** A lead's stage lives in the store, not in
  chat or memory. Record it with `add` / `set_stage` and report what `pipeline`
  / `list` return.
- **Advance with `set_stage`, not `set`.** `set_stage` validates the stage and
  logs the `stage_change` so the history is intact. A stage hand-applied with
  `set` drops the audit trail.
- **Never hand-count the pipeline.** Run `pipeline` and report its counts.
- **Capture the source.** Always record where a lead came from (`beta-signup`,
  `referral`, `manual`, …) so attribution is queryable.
- **Heed the dedup warning.** `add` rejects an exact-duplicate email and warns
  on a near-duplicate name — confirm with the user before creating a second
  record for the same person.
- **Log meaningful touches.** Use `log` to record emails, calls, and notes so a
  lead's history is reconstructable.

## Privacy

These are real people's contact details. Never forward a lead's specifics to a
third party (see the persona guardrails).
