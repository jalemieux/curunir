---
name: dreaming
description: "Use when the scheduler or user asks to tidy, organize, or reconcile the memory directory (\"dreaming\", memory housekeeping). Audits context/memory/ against its README and fixes organization — registers new files, corrects naming and placement, repairs references — without ever editing the content of memory files."
hidden: true
---

# Dreaming

The periodic housekeeping pass for `context/memory/`. Dreaming keeps the memory
directory **organized**: every file registered, named, and placed according to
the conventions written in that directory's own `README.md`.

Dreaming reorganizes; it never rewrites.

## The boundary — read this first

There is exactly one hard line, and it is **structural**:

```
DREAMING MAY CHANGE (wiring)            DREAMING MUST NEVER TOUCH (facts)
────────────────────────────           ─────────────────────────────────
• file names        (rename)            • the body / prose / facts inside
• file locations    (move)                any memory data file:
• README.md (Taxonomy table, the           profile.md, preferences.md,
  "Where to look first" routing list)      core-insights.md, projects.md,
• files under summaries/                   tasks.md, people/*.md,
• references and links that point at       idea-log.md,
  memory files                             archives/idea-log-archive.md,
                                           archives/conversations/*.md
```

The test for every edit: **"is this a fact, or is it wiring?"** Moving a file,
renaming it, registering it, fixing a link to it — wiring. The sentences and
facts *inside* a data file — frozen. You may not touch them.

**The one allowed exception:** if a data file's prose contains a stale *path
reference* (e.g. `projects.md` says "see `recipes.md`" after `recipes.md` was
renamed), you may fix that path. A path is wiring. You may fix the path and
nothing else on that line.

**The one sanctioned content move — idea-log aging.** If `idea-log.md` exists
and holds an entry with status `Spark` or `Monitoring` whose `Last touched`
date is more than 90 days old, move that entry — its pipeline-table row and
its detail block, **verbatim** — to `archives/idea-log-archive.md` (create the
archive if absent, and keep the "Dormant ideas: see
`archives/idea-log-archive.md`" pointer line at the bottom of `idea-log.md`).
The only permitted edits during the move: set the entry's Status to `Dormant`
and append an `Archived: <date>` stamp. Relocation plus those two fields is
the entire operation — never rewrite, expand, condense, or "improve" the entry
body on the way. "May archive verbatim" is not "may edit." Sparks are the
owner's words; inflating one into an analysis is exactly the drift this
boundary exists to prevent.

If you are ever unsure whether an edit crosses the line, do not make it —
record it in the report (Step 4) as something a human should decide.

## Why this is a skill and not a script

"Expected organization" is not a schema — it is the prose conventions in
`context/memory/README.md`. Deciding *where* a new file's row belongs in the
Taxonomy table, *what* its purpose is, and *whether* it answers a recurring
owner question all require reading and understanding. That is the job.

## Inputs

- **Date** — today, `YYYY-MM-DD`. Use `date -u +%F`.
- **Memory directory** — `context/memory/`. If it does not exist, there is
  nothing to do: write nothing, exit, and report "memory directory not present."

---

## Step 1 — Snapshot (git, local-only)

`context/memory/` is protected by a **local-only** git repository. It exists
solely as an undo mechanism for dreaming. The memory directory is private.

> **Never** run `git remote add`, `git push`, or any command that sends this
> repository anywhere. It has no remote and must never get one.

1. If `context/memory/.git` does not exist, initialize it:
   ```bash
   git -C context/memory init
   ```
2. Commit everything currently on disk as the restore point. This captures any
   content the memory extractor wrote since the last dreaming run — so it is
   *not* mixed into dreaming's own commit:
   ```bash
   git -C context/memory add -A
   git -C context/memory -c user.name=dreaming -c user.email=dreaming@localhost \
     commit -m "pre-dream snapshot $(date -u +%F)" || true
   ```
   `|| true` because a clean tree (nothing to snapshot) is fine. The `-c` flags
   set identity inline — do **not** modify git config.

After this step the working tree is clean, so anything that follows is
attributable to dreaming alone.

## Step 2 — Survey

1. **Read `context/memory/README.md` in full.** It is the source of truth for
   expected organization: the Taxonomy table (the canonical file list), the
   "Where to look first" routing list, and the naming/placement conventions
   (e.g. "`people/` — one file per person, lowercase-hyphenated").
2. **List what is actually on disk:**
   ```bash
   git -C context/memory ls-files
   ```
   (Use `ls-files` so the `.git` directory is excluded automatically.)
3. Hold the two side by side. The discrepancies drive Step 3.

## Step 3 — Reconcile

Work through each discrepancy type. After each fix, the tree must be left
*consistent* — never half-done.

### 3a. Unregistered file

A root-level `.md` file that is not present in the README Taxonomy table.
(Scope this check to **root-level** files and `people/*.md`. Files under
`summaries/` and `archives/` are an auto-maintained tier and are not registered
individually.)

- Read the file to learn what it actually holds.
- Add a row to the Taxonomy table, placed **among the files of its kind** — a
  root topical file goes with the other root topical files, not appended after
  the non-memory directories at the table's end.
- Write an **accurate** Purpose cell from the file's real content. Do not write
  a placeholder.
- If the file answers a recurring owner question, also add it to the "Where to
  look first" list, in a sensible position.

### 3b. Naming violation

A file whose name breaks a convention (e.g. `people/Anna Smith.md` should be
`people/anna-smith.md`).

- Rename with `git -C context/memory mv "<old>" "<new>"` so history follows.
- **Immediately** re-point every inbound reference. Find them:
  ```bash
  grep -rn "<old-filename>" context/memory --exclude-dir=.git
  ```
  Update the Taxonomy table, the routing list, index files under `summaries/`,
  and any stale path reference in prose (path only — see the boundary rule).

### 3c. Misplaced file

A file in the wrong location (e.g. a person record sitting at the memory root
instead of in `people/`).

- Move it with `git -C context/memory mv`.
- Re-point references exactly as in 3b.

### 3d. Dangling reference

A Taxonomy row, routing-list entry, or index entry that points at a file no
longer on disk.

- Remove or correct the **reference**. Do **not** create a file to satisfy it,
  and do **not** delete any other file.

### The transaction rule

A rename or move is not finished until every reference to the file is updated
in the same pass. Leaving a dangling reference manufactures the exact drift
dreaming exists to eliminate. Rename-and-re-point is **one** step, not two.

## Step 4 — Report

Overwrite `context/memory/summaries/dreaming.md` with a fresh report each run:

```markdown
# Dreaming — <YYYY-MM-DD>

## Changed
- Registered `recipes.md` in the Taxonomy table.
- Renamed `people/Anna Smith.md` → `people/anna-smith.md` (3 references updated).

## Needs a human
- `notes.md` at the memory root looks like it may belong in a subdirectory,
  but its intended location is ambiguous — left in place.

## Result
Clean — or — N changes applied.
```

If dreaming changed nothing, still write the report with an empty "Changed"
section and `Result: clean — no drift found`.

## Step 5 — Commit

Commit dreaming's structural changes as a single, separate commit:

```bash
git -C context/memory add -A
git -C context/memory -c user.name=dreaming -c user.email=dreaming@localhost \
  commit -m "dreaming: <one-line summary of what changed>" || true
```

Because Step 1 already committed everything prior, this commit's diff is
**only** dreaming's work — so `git -C context/memory revert <this commit>`
cleanly undoes a bad pass without losing any extracted facts.

---

## Output checklist

Before finishing, confirm in your reasoning:

- [ ] Step 1 ran — `context/memory/` is a git repo and the pre-dream snapshot
      commit was made (or the tree was already clean).
- [ ] `README.md` was read before any reconcile decision.
- [ ] Every rename/move had its references re-pointed in the same pass
      (no dangling references introduced).
- [ ] No memory data file's body content was edited (path-reference fixes and
      the verbatim idea-log Dormant-archival move only).
- [ ] `summaries/dreaming.md` was written.
- [ ] Step 5 committed dreaming's changes as a separate commit.

## Hard rules

- **Never** edit the body/prose/facts of a memory data file. The only edits
  permitted inside a data file are correcting a stale path reference and the
  verbatim idea-log Dormant-archival move (status + `Archived:` stamp only).
- **Never** delete a memory file. Removing a dangling *reference* is fine;
  deleting a *file* is not.
- **Never** merge, split, or rewrite the content of files.
- **Never** rewrite README prose beyond adding/fixing Taxonomy rows and
  routing-list entries.
- **Never** add a git remote or push the memory repository anywhere.
- A run that finds nothing wrong is a success — write the report and stop.
  Do not invent work.
