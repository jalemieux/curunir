# Memory Index

Always-loaded routing table. Stays small. Points to indexes that grow.

## Where to find things

| Layer | File / dir | When to load |
|---|---|---|
| Taxonomy | `README.md` | Always (this is the index of indexes) |
| Owner facts | `preferences.md`, `projects.md`, `tasks.md`, `people/`, `core-insights.md` | On owner-related questions |
| Chronological history | `summaries/timeline.md` | "What did we discuss recently?" / "When did X happen?" |
| Topic history | `summaries/topics/<slug>.md` | "What have we discussed about X?" — slug matches a memory file (`projects`, `people-anna`, etc.) |
| Full conversation summaries | `archives/conversations/YYYY-MM-DD-<slug>.md` | When an index entry isn't detailed enough |

## Progressive discovery

```
README/MEMORY ──> summaries/timeline.md ──> archives/conversations/*.md
            └──> summaries/topics/<slug>.md ──┘
```

Read in this order: index → topic-or-timeline → archive. Don't load
`archives/` wholesale — grep or follow links from the indexes.

## Maintenance

`summaries/timeline.md` and `summaries/topics/*.md` are written automatically
by the memory-extraction background job. **Do not hand-edit them** — they will
be regenerated. Hand-edit the topical files (`preferences.md`, etc.) and
`README.md` instead.
