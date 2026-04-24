# Onboarding

Generates a personalized `context/identity.md` from a short questionnaire so a
new user gets a useful agent on first run instead of the maintainer's default
persona.

## How to onboard

```bash
# 1. Fill out the questionnaire (open in your editor of choice)
$EDITOR onboarding/questions.md

# 2. Render answers into context.default/identity.md
python onboarding/render.py

# 3. Start curunir — bootstrap copies the seeded identity into context/
python run.py
```

That's it. If you skip step 1, render still works — you'll get a defaults
identity that asks you for your name on first interaction.

## Files

| File | Purpose |
|---|---|
| `questions.md` | The 8-question questionnaire. User edits this. |
| `identity_template.md` | Template for the generated identity.md, with `{{placeholders}}`. |
| `render.py` | Parses `questions.md` answers, fills the template, writes `context.default/identity.md`. |
| `bootstrap.py` | Copies any file in `context.default/` to `context/` on first run. Never overwrites existing files. |

## Pipeline

```
questions.md  ──► render.py  ──►  context.default/identity.md
                                              │
                                              │  (first run only, via bootstrap.py)
                                              ▼
                                       context/identity.md
```

## Re-running

- **Re-running `render.py`** is safe — it only rewrites `context.default/identity.md`.
- **`context/identity.md` is never overwritten** by either script. Once
  bootstrap has copied the file into `context/` on first run, your live
  persona belongs to you. To reset: edit `context/identity.md` directly, or
  delete it and re-run `python run.py` to re-bootstrap from the latest
  `context.default/identity.md`.

## Customizing personas

The 6 named personas (Pragmatic peer, Executive assistant, Stoic butler,
Friendly concierge, Witty companion, Chief of staff) are defined inline in
`render.py` as the `PERSONAS` dict. Add a new flavor by adding a dict entry
keyed by the normalized name (lowercase, no whitespace/hyphens). Or just
write freeform text in Q7 — it's passed through verbatim if it doesn't match
any known flavor.

## Design

See [`docs/superpowers/specs/2026-04-23-onboarding-questionnaire-design.md`](../docs/superpowers/specs/2026-04-23-onboarding-questionnaire-design.md)
for the rationale behind question selection, defaults, and structural choices.

Tracking: [#26](https://github.com/jalemieux/curunir/issues/26).
