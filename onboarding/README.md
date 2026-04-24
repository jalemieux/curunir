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

   > Read `onboarding/questions.md` and write `context.default/identity.md`
   > based on my answers. Follow the section structure of the existing
   > `context.default/identity.md`: a one-sentence opening introducing
   > curunir and the user, then `## Core Traits` (bullets — manner,
   > substance style, how to address the user, timezone), `## Capabilities`
   > (prose + bulleted standing jobs the user wants help with),
   > `## Guidelines` (bullets — rules of engagement, citation conventions,
   > consent boundaries), and the existing `## Memory`, `## Scheduling`,
   > and `## Creating Skills` sections kept verbatim. Infer what's implicit,
   > reconcile tensions between answers (e.g., "detailed" substance +
   > "terse" manner), and normalize anything ambiguous (timezone strings,
   > etc.). Do not touch `context/identity.md`.

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
| `questions.md` | The 8-question questionnaire. User edits this. |
| `bootstrap.py` | Copies any file in `context.default/` to `context/` on first run. Never overwrites existing files. |
| `README.md` | This file. |

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
