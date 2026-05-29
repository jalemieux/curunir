# Onboarding

Generates a personalized `context/identity.md` from a short questionnaire so a
new user gets a useful agent on first run instead of the maintainer's default
persona.

The identity file is **written by an LLM**, not a template engine. You fill
out the questionnaire, then hand it to the model with the prompt below. This
lets the model reconcile tensions in your answers (e.g., "detailed" substance
+ "terse" manner), normalize sloppy input (e.g., `America/NewYork` →
`America/New_York`), and expand short notes into usable conventions (e.g.,
"cite WSJ/FT" → a citation convention section).

## How to onboard

1. **Fill out the questionnaire:**
   ```bash
   $EDITOR onboarding/questions.md
   ```
   Skip anything that doesn't apply. Free-form prose under each question is
   fine — you don't need to preserve any markers.

2. **Ask an LLM to generate the identity file.** Any capable assistant with
   filesystem access works (curunir itself, Claude Code, Cursor, etc.). Use
   a prompt like:

   > Read `onboarding/questions.md` and fill
   > `context.default/identity.md`. The file is a bare skeleton — it
   > has only two empty headings (`## Identity` and `## Personality`)
   > and no body. You own the whole file. Do not touch
   > `context.default/behavior.md` — that file is the agent's
   > operating defaults (capabilities, guidelines, deliverables,
   > workspace, memory, scheduling, skill creation) and is
   > intentionally outside the onboarding flow.
   >
   > Produce three pieces of content drawn from the questionnaire:
   >
   > - **Opening sentence** (prepended above `## Identity`) — one
   >   sentence introducing the agent and the user. Format:
   >   `You are <agent name>, <one-clause disposition> for <owner name> — <owner role/focus>.`
   >   Agent name and disposition from q7/q7b; user's name from q1;
   >   user's domain from q2.
   > - **`## Identity` body** — one line: `- **Name:** <agent name>` (q7b).
   > - **`## Personality` body** — 2–5 sentences of second-person prose
   >   covering voice (warmth/formality/register from q7), default
   >   response length (q5), and stance (deferential / peer /
   >   proactive / etc., derived from q7's chosen flavor and q6's
   >   permission-asking preference). One prose block, no bullets, no
   >   numeric scales.
   >
   > Write in **second person** ("You speak in…"). Infer what's
   > implicit, reconcile tensions between answers (e.g., "detailed"
   > substance + "terse" manner = detailed in *what*, terse in *how*),
   > and normalize ambiguous input (timezone strings, etc.). Do not
   > touch `context/identity.md`.

   Review the output. Iterate if anything misses — the model will happily
   revise.

3. **Start curunir.** Bootstrap copies the seeded identity into `context/`
   on first launch.
   ```bash
   python run.py
   ```

## Files

| File | Purpose |
|---|---|
| `questions.md` | The questionnaire (8 questions + 7b for the agent's name). User edits this. |
| `bootstrap.py` | Copies any file in `context.default/` to `context/` on first run. Never overwrites existing files. |
| `README.md` | This file. |

The agent's system prompt is assembled from two files in `context/`:
`identity.md` (persona — what the onboarding LLM writes) and
`behavior.md` (operating defaults — shipped as-is from
`context.default/`). The `/identity` skill only edits the persona file;
`behavior.md` is hand-edited.

`context.default/identity.md` is the source of truth for the persona
file's shape (an opening sentence followed by `## Identity` and
`## Personality`). The LLM prompt above mirrors that shape; if you
change the skeleton, change the prompt to match.

The generated identity lives at `context.default/identity.md` (versioned, a
reasonable default you can ship) and gets copied to `context/identity.md` on
first run (your live persona, never overwritten afterward).

## Pipeline

```
questions.md  ──►  [ LLM ]  ──►  context.default/identity.md
                                          │
                                          │  (first run only, via bootstrap.py)
                                          ▼
                                   context/identity.md
```

## Re-running

- **Re-generating `context.default/identity.md`** is safe — it's a seed file,
  not the live one.
- **`context/identity.md` is never overwritten** by bootstrap. Once your live
  persona exists, it belongs to you. To reset: edit `context/identity.md`
  directly, or delete it and re-run `python run.py` to re-bootstrap from the
  latest `context.default/identity.md`.

## Migrating an existing `context/memory/`

Curunir's memory layout changed in #102: owner identity facts moved to
`profile.md` and `preferences.md` became working/communication style only.
If your `context/memory/preferences.md` predates that split it likely holds
both kinds of fact mixed together.

`scripts/migrate_memory_layout.py` splits the file in place without data
loss:

```bash
python scripts/migrate_memory_layout.py --dry-run   # preview the split
python scripts/migrate_memory_layout.py             # apply
```

The script asks the LLM only to *classify* each `## Section` as profile or
preferences — section text is sliced verbatim from the source, never
rewritten. The original `preferences.md` is backed up to
`preferences.md.bak.<timestamp>` before anything is overwritten, and the
script aborts if any section would end up in neither output (or both).

## Why LLM instead of a template

A template can only mechanically substitute. An LLM can:

- Reconcile conflicting answers (detailed substance + terse manner = detailed
  in what, terse in how).
- Catch and fix malformed input (invalid timezone strings, missing
  punctuation, list/prose mixing).
- Expand a short note ("cite WSJ and academic lit with links") into a proper
  conventions section.
- Adapt the output's own tone to match the persona the user described.

The earlier `render.py` + `identity_template.md` approach has been removed;
its failure mode was silent — if the user didn't preserve an `_Answer:_`
marker, answers were dropped and defaults shipped without warning.

## Design

See [`docs/superpowers/specs/2026-04-23-onboarding-questionnaire-design.md`](../docs/superpowers/specs/2026-04-23-onboarding-questionnaire-design.md)
for the original rationale behind question selection. Note: that spec
describes the prior `render.py` pipeline; the current generation step is
LLM-driven as documented above.

Tracking: [#26](https://github.com/jalemieux/curunir/issues/26).
