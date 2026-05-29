---
name: podcast-ingest
description: "Pull, transcribe, summarize, store, and index podcast episodes (YouTube-sourced) into context/workspace/podcasts/. Modes: pull (weekly cron), add/list/remove/edit (config management), backfill (first-time catch-up for one show), rebuild-indexes (recovery). Hidden — invoked only via /podcast-ingest, the scheduler, or by explicit name."
hidden: true
---

# Podcast Ingest

Ingest YouTube-sourced podcast episodes into `context/workspace/podcasts/`
as a searchable corpus: per-episode markdown (frontmatter + plain-text
transcript), plus progressive-discovery indexes (`timeline.md`,
`by-podcast/<slug>.md`, `topics/<slug>.md`).

The skill is a runbook, not Python. It uses `bash` (for `yt-dlp`), `read`
(to load transcripts that exceed bash's 30k-char output cap), `write` /
`edit` (for per-episode files, the config, the ledger, and the indexes),
the `youtube-transcript` skill (for the exact transcript pipeline), and
LLM calls (for host/guest extraction, summary, and topic tagging).

## Pick a mode

The invoking message tells you which mode to run. If it's ambiguous, ask.

| Mode | Trigger | What it does |
|------|---------|--------------|
| `pull` | scheduler, or "run podcast-ingest in pull mode" | Weekly: list new episodes per show, filter, transcribe, store, index |
| `add <name or URL>` | "add the All-In podcast" | Interactive: surface candidate channel, sample episodes, propose filter, write config |
| `list` | "list my podcasts" | Print configured shows |
| `remove <slug>` | "remove all-in" | Delete entry from `podcasts.yaml` (prompt before touching files) |
| `edit <slug>` | "edit acquired" | Re-run filter-discovery against an existing entry |
| `backfill <slug> [--episodes N]` | "backfill acquired with 5 episodes" | Ingest the most recent N (default 10) filtered, unseen episodes |
| `rebuild-indexes` | "rebuild the podcast indexes" | Re-derive summary indexes from raw transcript files on disk |

## Corpus layout (locked — do not deviate)

```
context/workspace/podcasts/
  README.md                              # routing entry: what's here, how to navigate
  podcasts.yaml                          # per-show config
  .seen-ids.txt                          # append-only ledger of processed YouTube IDs
  summaries/
    timeline.md                          # newest-first, one entry per episode across all shows
    by-podcast/
      all-in.md                          # newest-first, one entry per episode of this show
      acquired.md
    topics/
      ai-policy.md                       # newest-first, entries from any show tagged with this topic
      semiconductors.md
  all-in/
    2026-05-22-e212-foo.md               # per-episode file: frontmatter + plain-text transcript
  acquired/
    2026-05-20-tsmc.md
```

Per-episode file (the downstream consumer's contract — match exactly):

```markdown
---
podcast: All-In
episode_title: 'E212: Nvidia earnings, EU AI Act, weekend reads'
date: 2026-05-22
hosts: [Chamath Palihapitiya, Jason Calacanis, David Sacks, David Friedberg]
guests: []
url: https://www.youtube.com/watch?v=...
---
[plain-text transcript here — no timestamps, no speaker labels]
```

**Summaries do not live in the per-episode file.** They live in the
index files alongside the link to the raw transcript. This mirrors the
memory layout (`src/memory_indexer.py`): `README.md` routes → indexes
give cheap browsable summaries with topic tags → raw transcript loaded
on demand.

Each index entry has the same shape across `timeline.md`,
`by-podcast/<slug>.md`, and `topics/<topic>.md`:

```markdown
### 2026-05-22 — All-In E212: Nvidia earnings, EU AI Act, weekend reads
**Topics:** ai-policy, semiconductors, markets
- Nvidia Q1 results beat consensus; data-center revenue mix shifts
- EU AI Act enforcement delayed six months after industry pushback
- Hosts debate whether private-credit growth signals systemic risk
[transcript](../all-in/2026-05-22-e212-foo.md)
```

The transcript link is **relative to the index file's directory** —
`../all-in/...` from `summaries/timeline.md`, `../../all-in/...` from
`summaries/topics/<topic>.md`.

## Config (`podcasts.yaml`)

```yaml
- name: All-In
  slug: all-in
  source: https://www.youtube.com/@allin
  include_pattern: '^E\d+:'
  # exclude_pattern: optional regex; subtracts from include matches
  # known_hosts: filled in after the first ingest (see step 3 of per-episode ingest)
- name: Acquired
  slug: acquired
  source: https://www.youtube.com/@AcquiredFM
  # no filter — take everything from this source
```

- `source` is a channel URL or a playlist URL. If a curator-maintained
  playlist already contains exactly the episodes the user wants, point
  at the playlist and skip the regex.
- `include_pattern` / `exclude_pattern` are Python regex matched against
  the episode title.
- `slug` is kebab-case; it's the directory name and the index file name.

If the file is missing on first run, create it with an empty list (`[]`)
and an explanatory comment.

---

## Mode A — `pull` (the weekly cron path)

Invoked by the scheduler with: *"Run the podcast-ingest skill in pull mode."*

1. Read `context/workspace/podcasts/podcasts.yaml`. If missing or empty,
   report "no podcasts configured" and exit.
2. For each show:
   1. List recent uploads:
      ```bash
      CUTOFF=$(date -u -v-8d +%Y%m%d 2>/dev/null || date -u -d '8 days ago' +%Y%m%d)
      yt-dlp --flat-playlist \
             --print '%(id)s\t%(title)s\t%(upload_date)s' \
             --dateafter "$CUTOFF" \
             "<source>"
      ```
      8 days (not 7) gives a one-day overlap on either side of the
      weekly cron; the `.seen-ids.txt` ledger handles deduping.
   2. Drop rows whose ID is already in `context/workspace/podcasts/.seen-ids.txt`.
   3. Apply `include_pattern` / `exclude_pattern` to the title (Python
      regex semantics — use `python3 -c` or grep -E with care if
      anchoring matters).
   4. For each surviving episode, run **Per-episode ingest** below.
3. After all shows are processed, append a one-paragraph report to the
   conversation: *"Ingested N new episodes: <show> <title>, <show>
   <title>, …"* Mention any shows that errored out.

**Failure isolation:**
- Per-show yt-dlp/network failure → log, skip the show, continue. Retried next week.
- Per-episode failure (download, LLM, write) → log, skip the episode,
  continue. ID **not** added to `.seen-ids.txt` so it retries next week.

## Per-episode ingest (shared by Mode A and Mode D)

Inputs: video `<id>`, `<title>`, `<upload_date>` (YYYYMMDD), `<show>` (the config entry).

### 1. Fetch the transcript

Use the exact pipeline from the `youtube-transcript` skill — load it
with `load_skill("youtube-transcript")` if you haven't already, and
follow its warnings (especially: **never pipe the transcript through
bash stdout** — the bash tool truncates at 30k chars).

```bash
yt-dlp --write-sub --write-auto-sub --sub-lang en --skip-download \
       --sub-format vtt \
       --output "/tmp/yt_%(id)s" \
       "https://youtu.be/<id>"

awk '
  /^WEBVTT/ || /^Kind:/ || /^Language:/ {next}
  /^[0-9]+$/ {next}
  /-->/ {next}
  /^$/ {next}
  /<[0-9:.]+>/ {next}
  {print}
' /tmp/yt_<id>.en.vtt \
  | awk '!seen[$0]++' \
  | sed 's/&gt;/>/g; s/&lt;/</g; s/&amp;/\&/g; s/&#39;/'"'"'/g' \
  > /tmp/yt_<id>.txt

wc -l -w -c /tmp/yt_<id>.txt
```

Then `read(file_path="/tmp/yt_<id>.txt")` to load it into context.

If the `.vtt` file doesn't exist (no captions), log and skip the episode.

### 2. Fetch metadata

```bash
yt-dlp --skip-download --print-json "https://youtu.be/<id>" > /tmp/meta_<id>.json
```

`read` it. You need `upload_date`, `channel`, `title`, and `description`.

### 3. Extract hosts and guests

If the show's config has `known_hosts:`, reuse that list and only run
the LLM to extract `guests` from the title + description.

Otherwise, single LLM call with this shape (do this inline in your
reasoning — there's no dedicated tool):

> Given this podcast title and description, return two YAML lists:
> `hosts:` — the recurring co-hosts of the show (drawn from your
> knowledge of the show, *not* this episode's guests). `guests:` — the
> guests appearing in this specific episode. Names only. If unsure,
> empty list.
>
> Title: `<title>`
> Description: `<description>`

After the first successful extraction for a show, write `known_hosts:`
back to that show's entry in `podcasts.yaml` so subsequent episodes
skip the host step. Use the `edit` tool on the YAML file — don't
rewrite the whole file.

### 4. Summarize and tag topics

Single LLM call against the **transcript** (which you loaded in step 1):

> Produce a 5–10 bullet summary of the substantive points discussed in
> this episode. Then produce a list of 2–5 kebab-case topic slugs
> (lowercase, hyphenated, no punctuation) capturing the themes.
>
> Prefer slugs from this existing taxonomy if any fit; only invent a
> new slug when nothing in the list matches: `<list contents of
> summaries/topics/ if the dir exists>`
>
> Return:
> ```
> SUMMARY:
> - ...
> - ...
> TOPICS: slug-a, slug-b, slug-c
> ```

The summary and topics feed step 6 (index update), **not** the
per-episode file.

### 5. Write the per-episode file

Path: `context/workspace/podcasts/<slug>/<YYYY-MM-DD>-<episode-slug>.md`.

- `<YYYY-MM-DD>` = upload date as ISO.
- `<episode-slug>` = title lowercased, non-alphanumerics replaced with
  `-`, runs of `-` collapsed, leading/trailing `-` stripped, truncated
  to ~60 chars at a word boundary.

Body:

```markdown
---
podcast: <show.name>
episode_title: '<title with single quotes doubled>'
date: <YYYY-MM-DD>
hosts: [<comma-separated, no quotes if name has no comma/colon>]
guests: [<...>]
url: https://www.youtube.com/watch?v=<id>
---
<plain-text transcript from /tmp/yt_<id>.txt>
```

YAML quoting: episode titles often contain `:`, so single-quote them
and double any embedded single quote. Lists with comma-containing
names: quote individual entries.

Use `write` (not `edit`) — this is a fresh file each time.

### 6. Update indexes

Build one entry (the markdown block under "Each index entry…" at the
top of this file) and upsert it into three places:

- `summaries/timeline.md` — newest-first across all shows. Transcript
  link is `../<slug>/<filename>.md`.
- `summaries/by-podcast/<slug>.md` — newest-first within this show.
  Transcript link is `../../<slug>/<filename>.md`.
- For each topic slug from step 4: `summaries/topics/<topic>.md` —
  newest-first across all shows tagged with this topic. Transcript
  link is `../../<slug>/<filename>.md`.

**Upsert key is the transcript file path.** If an entry referencing
that path already exists in the file, replace it in place; otherwise
insert it before the first existing dated entry (so the file stays
newest-first); otherwise (empty file) write it after the file's
header.

First-time creation headers:

```markdown
# Podcast Timeline

Newest first. One entry per episode across all configured shows.
Each entry links to the raw transcript file.

```

```markdown
# All-In

Newest first. Every ingested episode of All-In.

```

```markdown
# Topic: ai-policy

Newest first. Episodes from any show that touched this topic.

```

Use `edit` on existing files (read first, then edit) and `write` for
new files.

### 7. Mark the episode seen

**Only after step 5 succeeds** (the raw file is on disk), append the
YouTube ID to `context/workspace/podcasts/.seen-ids.txt`:

```bash
echo "<id>" >> context/workspace/podcasts/.seen-ids.txt
```

If step 6 fails after step 5 succeeded, the episode is still marked
seen — the indexes can be repaired with Mode E. If step 5 fails, the
ID is not appended, and the next run retries cleanly.

### 8. Clean up

```bash
rm -f /tmp/yt_<id>.en.vtt /tmp/yt_<id>.txt /tmp/meta_<id>.json
```

---

## Mode B — `add <show name or URL>`

User says *"add the All-In podcast"*. Run this dialogue:

1. **Resolve the source.**
   - If the user gave a URL, use it.
   - If they gave a name, run `yt-dlp 'ytsearch5:<name> podcast' --flat-playlist --print '%(uploader)s\t%(channel_url)s\t%(title)s'` to surface candidate channels. Present the top 5 and ask which is correct.
2. **Sample the recent feed:**
   ```bash
   yt-dlp --flat-playlist \
          --print '%(id)s\t%(title)s\t%(upload_date)s' \
          "<source>" | head -20
   ```
3. **Describe the pattern you see** to the user. ("Looking at the last
   20 uploads: there are numbered weekly recaps `E210:`, `E211:`, `E212:`,
   mixed with interview titles like `Marc Andreessen on …` and special
   episodes. Which do you want to ingest each week?")
4. **Propose a filter:**
   - `include_pattern`, and optionally `exclude_pattern`, as Python regex.
   - Run the proposed regex against the 20-episode sample and show the
     user: which titles match (will be ingested), which are excluded.
   - If the show has a curator-maintained playlist that already
     contains exactly what the user wants, suggest using the playlist
     URL as `source` and skipping the regex.
5. **Iterate steps 3–4** until the user signs off.
6. **Slug:** propose a kebab-case slug derived from the show name.
   Confirm with the user.
7. **Write the entry** to `podcasts.yaml` (create the file if missing).
8. **Offer backfill:** "Want me to backfill the last 5 episodes? I can
   run that now." If yes → Mode D.

## Mode C — `list` / `remove <slug>` / `edit <slug>`

- **`list`** — read `podcasts.yaml` and print: name, slug, source,
  include/exclude patterns. If the file is missing, say so.
- **`remove <slug>`** — read `podcasts.yaml`, find the entry, delete
  it (use `edit`). Then ask: "Also remove `context/workspace/podcasts/<slug>/`
  and its index entries?" Default is **no** — leave them in place
  unless the user confirms. If confirmed: `rm -rf` the show dir, and
  edit `summaries/timeline.md` / `summaries/by-podcast/<slug>.md` /
  every `summaries/topics/*.md` to drop entries that reference
  `<slug>/...` paths.
- **`edit <slug>`** — re-run Mode B's filter-discovery dialogue (steps
  2–5) against the existing entry, then update the YAML.

## Mode D — `backfill <slug> [--episodes N]`

Same loop as Mode A but for **one show**, with no date cutoff:

```bash
yt-dlp --flat-playlist \
       --print '%(id)s\t%(title)s\t%(upload_date)s' \
       "<source>" | head -<N>      # default N=10
```

Drop seen IDs, apply the filter, run **Per-episode ingest** for each
survivor. Default `N=10`. Use this for first-time setup of a newly
added show.

## Mode E — `rebuild-indexes`

Recovery path when the summary indexes drift out of sync with the raw
transcript files on disk.

1. Find every per-episode file: `find context/workspace/podcasts -mindepth 2 -maxdepth 2 -name '*.md' | grep -v '^context/workspace/podcasts/summaries/'`
2. **Truncate** the index files (or move them aside as `.bak`):
   - `summaries/timeline.md`
   - everything under `summaries/by-podcast/`
   - everything under `summaries/topics/`
3. For each per-episode file:
   1. Parse the frontmatter (`podcast`, `episode_title`, `date`,
      `hosts`, `guests`, `url`).
   2. Look for an existing well-formed index entry referencing this
      file in the `.bak` files. If found, reuse it.
   3. Otherwise, regenerate the summary + topic slugs by running
      step 4 of the per-episode ingest against the transcript body.
   4. Upsert into `timeline.md`, `by-podcast/<slug>.md`, and every
      `topics/<topic>.md` it touches.
4. Report which files were rebuilt and which reused prior summaries.

Mode E does **not** touch `.seen-ids.txt` and does **not** re-fetch any
video. It works purely from what's on disk.

---

## README at the corpus root

If `context/workspace/podcasts/README.md` does not exist, create it on
first ingest. It is the routing entry point — the agent reads it
before drilling into the indexes:

```markdown
# Podcasts

Ingested podcast episodes. Managed by the `podcast-ingest` skill.

## Where to look first

1. **Browsing by date across all shows** → `summaries/timeline.md`
2. **Browsing one show** → `summaries/by-podcast/<slug>.md`
3. **Browsing by topic** → `summaries/topics/<topic>.md`
4. **Verbatim transcript of a specific episode** → `<slug>/<YYYY-MM-DD>-<episode-slug>.md`

## How it's structured

Index files contain a 5–10 bullet summary + topic tags per episode
plus a link to the raw transcript. Per-episode files contain the
locked frontmatter (`podcast`, `episode_title`, `date`, `hosts`,
`guests`, `url`) and the plain-text transcript body. Summaries live in
the indexes, not in the per-episode files.

## Config

`podcasts.yaml` lists configured shows. Add, edit, remove, or backfill
via `/podcast-ingest`.
```

## Scheduler entry

The weekly pull is wired up via `context/schedules.json`:

```json
{
  "id": "podcast-ingest-weekly",
  "cron": "0 8 * * 1",
  "skill": "podcast-ingest",
  "prompt": "Run the podcast-ingest skill in pull mode. Process all shows in podcasts.yaml against the last 8 days of episodes, filter, transcribe, summarize, store, and update indexes. Return a one-paragraph report.",
  "enabled": true
}
```

Monday 8am local. The `enabled: false` template in
`context.default/schedules.json` is a starting point — the user flips
it to `true` after configuring at least one show with Mode B.

## Common mistakes

- **Putting the summary in the per-episode file.** The downstream
  consumer assumes frontmatter + plain transcript only. Summary +
  topics live in the indexes.
- **Piping the transcript through bash stdout.** Bash truncates at
  30k chars. Always: pipeline → file → `read` tool. (Same rule as
  `youtube-transcript`.)
- **Appending an ID to `.seen-ids.txt` before the per-episode file is
  on disk.** A crash between the append and the write means the
  episode is silently lost next run. Append the ID last.
- **Rewriting `podcasts.yaml` to add `known_hosts:`.** Use `edit` —
  preserve user comments and ordering.
- **Computing the transcript link as an absolute path.** Index files
  reference transcripts relative to their own directory:
  `../<slug>/...` from `summaries/timeline.md`, `../../<slug>/...`
  from `summaries/by-podcast/` and `summaries/topics/`.
- **Skipping the regex preview in Mode B.** The whole point of the
  interactive add flow is the user gets to see what their filter
  matches against real data before committing. Don't propose a regex
  and write the config without showing the user the match preview.
- **Running Mode E without backing up the existing indexes.** Move
  them to `.bak` first so you can reuse prior summaries (and recover
  if regeneration goes wrong).
