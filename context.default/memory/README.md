# Memory System

This directory is Curunir's persistent memory about its **owner / primary user**.

## Owner

Owner identity (name, family, role) lives in `preferences.md`. Read that file
to learn who the user is.

## Where to look first

When the user asks "who am I?", "what do you know about me?", or anything that
requires knowing the owner — **always** read these in order before responding:

1. `MEMORY.md` — routing table; points at the right index for the question
2. `preferences.md` — name, age, family, role, working style, tool preferences, communication preferences
3. `projects.md` — current projects, status, architecture, relationships between them
4. `tasks.md` — open items / unresolved questions the user is working through
5. `people/*.md` — colleagues, collaborators, stakeholders the user works with

For questions about past conversations ("what did we discuss about X?"), go through
the indexes instead: `summaries/topics/<slug>.md` (if X matches a memory file) or
`summaries/timeline.md` (if you're orienting by time).

A single read on this README is **not** enough. Read the files above before
saying "I don't know who you are."

## Taxonomy

| File / dir | Purpose |
|---|---|
| `preferences.md` | Owner's identity (name, family, role) + working style + tool prefs |
| `projects.md` | Current projects with status, architecture, relationships |
| `tasks.md` | Open items needing resolution; unresolved questions; TODOs |
| `people/` | Colleagues, teams, contacts (one file per person, lowercase-hyphenated) |
| `core-insights.md` | Fundamental realizations about existence, consciousness, identity |
| `archives/conversations/YYYY-MM-DD-topic.md` | Dated conversation summaries |
| `MEMORY.md` | Small always-loaded routing table pointing at indexes |
| `summaries/timeline.md` | Auto-maintained chronological list of all archived conversations |
| `summaries/topics/<slug>.md` | Auto-maintained: archives that touched the entity named by `<slug>` |

## Workflow

1. **Orient** — read this README.
2. **Pull context** — for any owner-related question, read `preferences.md` and `projects.md` minimum.
3. **Search** — use `grep` across the directory for specific names, projects, or dates.
4. **Update** — when you learn something new about the owner, append to the right file. Update this taxonomy if you create a new file or category.

## Best Practice

The memory system is only as useful as its discoverability. **Always update
this taxonomy when creating new memory files or categories.** Read multiple
files when answering identity questions — the README is an index, not the answer.
