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

   > Read `onboarding/questions.md` and fill the placeholders in
   > `context.default/identity.md`. The file is a partial template —
   > its structure, boilerplate, and verbatim default lines are
   > already correct. **Edit only the HTML-comment placeholders**
   > (`<!-- … -->`). Leave every other line untouched, including the
   > `## Personality` preamble, `## Boundaries`, `## Capabilities`,
   > `## Guidelines`, `## Memory`, `## Scheduling`, and
   > `## Creating Skills`.
   >
   > For each placeholder, replace the comment with content drawn
   > directly from the questionnaire answers:
   >
   > - **Opening sentence** — one sentence introducing the agent and
   >   the user. Agent name and disposition from q7/q7b; user's name
   >   from q1; user's domain from q2.
   > - **`### Identity`** — the agent's name (q7b).
   > - **`### Voice & Stance`** — 4–8 lines of second-person prose
   >   covering four things together: (a) how the agent speaks,
   >   distilled from q5 (response length) and q7's axes (warmth /
   >   formality / initiative / humor / verbosity); (b) the disposition
   >   it brings to the user's domain, drawn from q2; (c) how it
   >   positions itself toward the user (deferential / peer /
   >   proactive / etc.), from q7's chosen flavor; (d) when it pauses
   >   to ask permission, from q6. No numeric scales.
   > - **`### Values & Quirks`** — standing convictions about how the
   >   agent does its work (citation style, preferred sources, working
   >   principles) and small habits/tells (input normalization,
   >   footnote-style asides, etc.), drawn from q3, q7, and q8.
   >   **Anchor every conviction in something the user actually
   >   wrote**; do not invent preferences they did not express, and do
   >   not carry over content from a prior fill of this file.
   > - **`## Standing Jobs`** — 2–4 bullets straight from q3, in the
   >   user's own framing.
   >
   > Write each persona subsection in **second person** ("You speak
   > in…", "You hold a few standing convictions…"). Infer what's
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

The `## Personality` schema (three subsections: `### Identity`,
`### Voice & Stance`, `### Values & Quirks`) lives in
`context.default/identity.md` itself — that file is the source of truth for
both the structure and the placeholder mapping. The LLM prompt above mirrors
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
