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
   > based on my answers. The current `context.default/identity.md` is the
   > **source of truth for the section structure** — match it exactly:
   >
   > - A one-sentence opening introducing curunir and the user.
   > - `## Personality` (the heart of the file) with seven labeled
   >   subsections in this order: `### Identity`, `### Voice`,
   >   `### Perspective`, `### Relationship`, `### Opinions`,
   >   `### Boundaries`, `### Quirks`. Each subsection is 2–5 lines of
   >   prose written in **second person** ("You speak in…", "You hold a
   >   few standing convictions…"). No numeric scales — descriptive prose
   >   only.
   > - Above the subsections, a short note that this block is the source
   >   of truth for curunir's voice, that user requests for tone shifts
   >   should be appended in-line so they persist, and that user
   >   overrides win over seeded defaults.
   > - `### Identity` must include: curunir's name, pronouns, a
   >   one-paragraph visual self-description (the kind of prose you'd
   >   feed to an image generator), and a relative path reference to
   >   `./avatar.png`. Pull these from question 7b.
   > - `### Voice` distills question 7's axes (warmth / formality /
   >   initiative / humor / verbosity) plus the response-length answer
   >   from question 5.
   > - `### Perspective` and `### Relationship` capture the requested
   >   *flavor* (e.g., "research assistant", "stoic butler") — what
   >   curunir reads the world like and how it positions itself toward
   >   the user.
   > - `### Boundaries` must include the consent rules from question 6
   >   AND this default line verbatim: *"Scheduled-task outputs
   >   (ai-digest, introspection, cron-driven prompts) suppress
   >   personality and prioritize utility — speak plainly and skip voice
   >   flourishes when the channel is system-task."*
   > - Then the existing `## Capabilities`, `## Guidelines`, `## Memory`,
   >   `## Scheduling`, and `## Creating Skills` sections — keep them
   >   tight and don't restate voice/formality there (those belong in
   >   `## Personality`).
   >
   > Infer what's implicit, reconcile tensions between answers (e.g.,
   > "detailed" substance + "terse" manner = detailed in *what*, terse
   > in *how*), and normalize ambiguous input (timezone strings, etc.).
   > Do not touch `context/identity.md`.

   Review the output. Iterate if anything misses — the model will happily
   revise.

3. **Generate curunir's selfie (optional but recommended).** The
   `### Identity` subsection holds a prose visual self-description. Use it
   to generate an avatar image:

   ```
   ### Identity description  ──►  [ image generator ]  ──►  context/avatar.png
   ```

   Any image tool works (ComfyUI Flux, Midjourney, DALL·E, etc.). Save the
   result as `context/avatar.png`. The image file is **not** loaded into
   the system prompt — only the description text is — so the file's role
   is purely for humans looking at the repo. The agent speaks coherently
   about its own appearance from the description alone.

   **Inverse direction (already have an image you want curunir to look
   like):** paste the image into a vision model, ask it to produce a one-
   paragraph prose description in the same style as the seeded
   `### Identity`, then drop that text into `context/identity.md`.

4. **Start curunir.** Bootstrap copies the seeded identity into `context/`
   on first launch.
   ```bash
   python run.py
   ```

## Files

| File | Purpose |
|---|---|
| `questions.md` | The questionnaire (8 questions + 7b for the avatar). User edits this. |
| `bootstrap.py` | Copies any file in `context.default/` to `context/` on first run. Never overwrites existing files. |
| `README.md` | This file. |

The `## Personality` schema (the seven subsections) lives in
`context.default/identity.md` itself — that file is the source of truth for
both the structure and the seeded defaults. The LLM prompt above mirrors
the schema; if you change the structure, change it there first.

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
