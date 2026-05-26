# ADR 0001 — Skill state tracking

- **Status:** Accepted (design only; implementation deferred to a follow-up issue)
- **Date:** 2026-05-26
- **Issue:** [#267](https://github.com/jalemieux/curunir/issues/267)
- **Implementation issue:** TBD (filed as the follow-up to this ADR)

## Context

Several skills need to persist *operational state* between runs — dedup ledgers, action logs, last-seen cursors. Today every such file lives under `context/memory/`, the same directory the memory indexer (`src/memory_indexer.py`) treats as narrative truth about the user. Two concrete examples in tree today:

| Skill | File(s) | Shape | Growth |
| --- | --- | --- | --- |
| `digest` | `context/memory/digest-<topic-slug>-sent.md` (one per topic; the schedule entry pins e.g. `digest-ai-sent.md`) | Append-only markdown table of `{date, url, decision}` rows. Read at Step 3 (dedup against last 7 days), appended at Step 4 (record what shipped). | Unbounded — every run adds rows. |
| `introspect` | `context/memory/introspection.md` | Append-only one-line-per-finding ledger: `{ts} \| {category} \| {action} \| {issue} \| {signature}`. | Unbounded — one line per scheduled tick, plus one per finding. |

Both files are deliberately structured (table, single-line records) and read programmatically by the skill itself. Neither is a fact about the user — they're bookkeeping. Living alongside `profile.md`, `preferences.md`, and the `summaries/` indexes, they:

1. **Pollute the indexer's input space.** `update_indexes()` walks archives in `context/memory/`. The bookkeeping files are not archives, but every new ledger format is one more thing the indexer's filters have to ignore.
2. **Force the agent to guess paths.** `digest` exposes a `Ledger path` input on every run; the schedule entries (`context/schedules.json`) have to pin the right filename for each topic. The convention is in the skill body, not the directory layout.
3. **Mix lifecycles.** Narrative memory is hand-edited and grows slowly; ledgers churn on every scheduled tick. Backups, audits, and `grep` against `context/memory/` all have to special-case the mixed content.

Issue #267 asks for an ADR picking an approach, defining the API and migrations, and setting a default TTL policy. This document is that ADR; the actual file moves and skill edits land in a follow-up issue.

## Decision

**Adopt a dedicated filesystem convention: `context/state/<skill>/`.**

```
context/state/<skill>/                  one directory per skill
  <namespace>.md                        append-only ledger; namespace = topic-slug, "ledger", a date, etc.
  <namespace>.json                      optional structured pointer (cursor / last-seen)
  README.md                             what this skill stores, TTL, schema
```

`<skill>` is the skill's `name:` from its `SKILL.md` frontmatter. `<namespace>` is whatever discriminator the skill already uses internally (topic slug for `digest`, a fixed string like `ledger` for single-namespace skills like `introspect`).

### TTL convention

The first line of every state markdown file declares its TTL as a comment:

```markdown
<!-- ttl: 7d -->
| date | url | decision |
| --- | --- | --- |
```

Defaults by file role:

| Role | Default TTL | Rationale |
| --- | --- | --- |
| Dedup ledger (append-only, e.g. `digest`) | **7d** | Matches `digest`'s existing 7-day rejection window. |
| Action ledger (append-only, e.g. `introspect`) | **30d** | Long enough to survive an oncall rotation; short enough that the file stays grep-able. |
| Cursor / pointer (single-row JSON) | **none** | Overwritten in place. |

### Pruning responsibility

Pruning is the **writing skill's** responsibility, performed as an idempotent step in the skill body (typically the same step that appends the new entry). No background daemon, no new tool, no cron sweeper. The TTL header is the contract; the skill body honors it. This keeps the system simple and inspectable — to debug "why is my ledger 50MB", you read the skill's prune step.

### Memory-indexer interaction

`src/memory_indexer.py` walks `context/memory/archives/conversations/` and rewrites `summaries/timeline.md` and `summaries/topics/<slug>.md`. The new tree under `context/state/` is **outside** that scope by construction; no indexer change is required. The indexer's exclusion is "the path doesn't start with `context/memory/`" — there is nothing to add to a deny-list.

## API / convention summary

For skill authors adopting this convention:

1. **Path.** Write to `context/state/<skill-name>/<namespace>.<ext>`. Create the directory on first run.
2. **Header.** Every markdown ledger starts with `<!-- ttl: Nd -->` on line 1. Pick from the defaults table above or document why you differ.
3. **Prune.** Before (or right after) appending, drop entries older than the declared TTL. Use the existing `bash` tool — no helper needed:
   ```bash
   # Drop rows older than 7d from a date-keyed ledger
   CUTOFF=$(date -u -v-7d +%F 2>/dev/null || date -u -d '7 days ago' +%F)
   awk -v cutoff="$CUTOFF" '/^<!--/||/^\|/{print;next} $1>=cutoff{print}' "$LEDGER" > "$LEDGER.tmp" && mv "$LEDGER.tmp" "$LEDGER"
   ```
4. **README.** Each `context/state/<skill>/` directory ships a one-page `README.md` describing the namespaces, schema, and TTL. Created at the same time as the first ledger write.
5. **No reads from outside the skill.** State is opaque to everyone else. If another skill wants the same data, it asks via the agent, not by reading the file.

## Migration

One-shot move when the follow-up implementation issue lands. `git mv` plus a path rewrite in the affected `SKILL.md` files. Existing ledger contents are preserved (the file is just renamed).

| Existing path | New path | Skill change |
| --- | --- | --- |
| `context/memory/digest-ai-sent.md` | `context/state/digest/ai.md` | Update `Ledger path` default in `skills/digest/SKILL.md` (Step 3 and Step 4 bash blocks). |
| `context/memory/digest-investment-positions-sent.md` | `context/state/digest/investment-positions.md` | Same — covered by the default-path change. |
| `context/memory/digest-<topic-slug>-sent.md` (any future topic) | `context/state/digest/<topic-slug>.md` | Default path is derived from the topic slug — no per-topic edit. |
| `context/memory/introspection.md` | `context/state/introspect/ledger.md` | Update the two ledger writes in `skills/introspect/SKILL.md` (Step 6 and the failure-mode log lines). |

`context/schedules.json` entries that pin `Ledger path:` explicitly need to be rewritten in the same PR. The follow-up issue should grep for `context/memory/digest-` in the user's `context/schedules.json` before declaring done.

The migration also recommends — but does not require — that `digest` **drop the `Ledger path` input entirely** and always derive `context/state/digest/<topic-slug>.md` from `Topic`. This removes a class of "wrong file pinned" bugs. Flagged as a behavior change worth verifying against current schedule entries first.

## Alternatives considered

### Approach 2 — Dedicated state tool (`state.read`, `state.append`, `state.prune`)

A new tool in `src/tools/` wrapping reads, appends, and TTL-aware pruning. Pros: a single place to enforce schema and TTL; opens the door to swapping the backend later. Cons: every skill author has to learn a new tool surface for something `read`/`edit`/`bash` already handle; the dispatcher gets a new entry; tests get a new mock. **Deferred.** Trigger to re-open: more than ~3 skills writing state, or any need to enforce schema centrally (e.g. structured rows queried across skills).

### Approach 3 — SQLite under `context/state.db`

A single file, transactional, queryable. Pros: trivial concurrency story; cross-skill queries become real; TTL pruning is one `DELETE WHERE ts < ?`. Cons: opaque to `grep`, which is how the current ledgers are debugged today; introduces a query-language surface to skill bodies; overkill at current scale (tens of rows per topic per week). **Deferred.** Trigger to re-open: parallel writers from multiple curunir processes against the same `context/`, or a feature that needs ad-hoc cross-skill queries.

### Approach 4 — Hybrid: filesystem for append-only, SQLite for cursors

Compromise that imports SQLite's complexity without the cross-skill query benefit. **Deferred.** Trigger to re-open: same as Approach 3 — only worth it if SQLite is already in the picture.

## Risks / open questions

- **Schedule-prompt churn at migration.** `context/schedules.json` is per-user and not in the repo. The follow-up PR's verification step must include "grep the user's `context/schedules.json` for `context/memory/digest-` and report any hits before claiming done."
- **Concurrency.** Filesystem append is naturally safe for the single-process scheduler. If we ever run multiple curunir instances against the same `context/`, append-with-flock or moving to Approach 3 becomes the trigger.
- **Portal surfaces.** `portal/` currently doesn't read ledger files. If a future portal view wants to surface "last digest sent at…", it should read from `context/state/<skill>/`. Calling this out so the convention spreads rather than getting re-invented.
- **Where do `extract-learnings` artifacts go?** They are *narrative* extractions, not bookkeeping; they stay under `context/memory/`. The line is "is this fact about the user/world, or is it an audit trail of a skill run?" — narrative stays in memory, audit trails move to state.

## Out of scope

- A general key-value store. State is per-skill, opaque to other skills.
- Replacing `context/memory/` for narrative facts. That tree, its indexer, and the extractor pipeline are unchanged.
- Backups, multi-host sync, encryption. `sync-context.sh` already handles `context/`; `context/state/` is a sibling and rides along for free.

## Acceptance against issue #267

1. **Approach picked?** Yes — Approach 1 (`context/state/<skill>/`).
2. **API / convention specified?** Yes — directory layout, TTL header, prune responsibility, README expectation.
3. **Existing ledgers and migrations listed?** Yes — see the migration table.
4. **Default TTL and pruning policy defined?** Yes — 7d / 30d / none by role; pruning is the writing skill's job.

Closes #267.
