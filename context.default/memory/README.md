# Memory System

This directory is your persistent memory. It holds three kinds of
information, kept in separate files so they don't bleed into each other:

1. **Owner facts** (`profile.md`) — who the user is.
2. **Owner preferences** (`preferences.md`) — how the user wants to work.
3. **Your own realizations** (`core-insights.md`) — what you have
   learned about yourself.

Your seeded persona — voice, perspective, boundaries, self-image — is
**not** in memory. It lives in `../identity.md` and is loaded into the
system prompt every turn.

## Where to look first

When the user asks "who am I?", "what do you know about me?", or anything
that requires knowing the owner — **always** read these in order before
responding:

1. `profile.md`
2. `preferences.md`
3. `projects.md`
4. `tasks.md`
5. `people/*.md`

See the Taxonomy table below for what each file holds.

For questions about past conversations ("what did we discuss about X?"), go through
the indexes instead: `summaries/topics/<slug>.md` (if X matches a memory file) or
`summaries/timeline.md` (if you're orienting by time).

A single read on this README is **not** enough. Read the files above before
saying "I don't know who you are."

## Taxonomy

| File / dir | Purpose |
|---|---|
| `profile.md` | Owner identity facts: name, pronouns, family, role, contact, addresses, medical notes |
| `preferences.md` | Owner's working style: response length, citation conventions, consent boundaries, tool prefs |
| `core-insights.md` | Your own accumulated realizations about how you operate |
| `projects.md` | Current projects with status, architecture, relationships |
| `tasks.md` | Open items needing resolution; unresolved questions; TODOs |
| `people/` | Colleagues, teams, contacts (one file per person, lowercase-hyphenated) |
| `archives/conversations/YYYY-MM-DD-topic.md` | Dated conversation summaries |
| `summaries/timeline.md` | Auto-maintained chronological list of all archived conversations |
| `summaries/topics/<slug>.md` | Auto-maintained: archives that touched the entity named by `<slug>` |

All paths in the table above are relative to this directory (`context/memory/`).

## Workflow

1. **Orient** — read this README.
2. **Pull context** — for any owner-related question, read `profile.md` and `preferences.md` minimum; add `projects.md` if the question touches work.
3. **Search** — use `grep` across the directory for specific names, projects, or dates.
4. **Update** — when you learn something new, append to the right file per the routing table above.
5. **Register** — if you create a *new* file in this directory (e.g. `recipes.md`), you **must**, in the same turn, add a row for it to the Taxonomy table above, and add it to the "Where to look first" list if it answers a recurring owner question. A new file that isn't registered in this README is invisible to future reads — it can only be found by `grep`. Creating the file and registering it are one step, not two.

## Best Practice

The memory system is only as useful as its discoverability. **A memory file
that is not listed in the Taxonomy table above does not exist** as far as
future reads are concerned — the agent orients off this README, not a
directory listing. So whenever you create a new memory file, register it
here in the same turn. Read multiple files when answering identity
questions — the README is an index, not the answer.

## Routing for the extractor

When extracting a new fact from a conversation, route by *what the fact is
about*:

- **About the owner as a person** (a new address, a family member, a job
  change, a medical note) → `profile.md`.
- **About how the owner wants to work** (a tone shift, a new citation
  preference, a consent rule) → `preferences.md`.
- **About yourself** (a recurring failure mode, a validated approach,
  a shift in self-perception) → `core-insights.md`.
- **About your own voice or persona** as a durable change → append to the
  relevant subsection in `../identity.md`, not memory.
- **A fact that fits none of the above** → route it to a new root-level
  topical file (e.g. `recipes.md`). When you do, you **must** also add a
  row for that file to the Taxonomy table above and, if it answers a
  recurring owner question, to the "Where to look first" list. A new file
  that isn't registered in this README is invisible to the agent — it can
  only be found by `grep`. Registering it is not optional.

