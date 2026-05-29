# Podcast Ingest Skill — Design

**Date:** 2026-05-25
**Status:** Drafted, awaiting user review

## Problem

The user follows a handful of "key podcasts" and wants their content
queryable inside curunir: pull new episodes weekly, transcribe them,
summarize them, and store both the raw transcript and the summary in a
form the agent can search. Today none of this is wired up, even though
curunir already has every ingredient: a cron scheduler, the
`youtube-transcript` skill (yt-dlp-based caption fetch), the
`memory_indexer` pattern for progressive-discovery indexes over a
markdown corpus, an LLM for summarization, and a separate downstream
skill the user is building that will consume the corpus.

The non-trivial part is that "which episodes count" is per-show. The
All-In channel publishes both the weekly numbered recap and a steady
stream of interviews; the user only wants the recap. So the pipeline
needs a per-show include/exclude filter, and adding a new show needs to
be an interactive flow where the user can see candidate episodes and
shape the filter against real data instead of writing regex blind.

## Goal

Ship a `podcast-ingest` skill that handles the full lifecycle —
discovery, filtering, transcription, summarization, storage, indexing —
plus interactive config management so the user can add, edit, list, and
backfill shows by talking to curunir.

Out of scope for v1:

- Non-YouTube podcast sources (RSS MP3 enclosures, Spotify-exclusive,
  Apple-exclusive). YouTube-only is sufficient for the user's target
  shows and avoids introducing a Whisper dependency.
- Cross-episode semantic search infrastructure (embeddings, vector
  store). The progressive-discovery markdown indexes are what the
  downstream skill is built against, and they are sufficient.
- Speaker diarization within transcripts. yt-dlp captions don't carry
  speaker labels and adding them would require a separate ASR pass.

## Corpus shape (locked)

The downstream skill the user is building assumes this exact layout —
the ingest pipeline must match it:

```
context/workspace/podcasts/
  README.md                              # routing entry: what's here, how to navigate
  podcasts.yaml                          # per-show config (see below)
  .seen-ids.txt                          # append-only ledger of processed YouTube IDs
  summaries/
    timeline.md                          # newest-first; one entry per episode across all shows
    by-podcast/
      all-in.md                          # newest-first; one entry per episode of this show
      acquired.md
    topics/
      ai-policy.md                       # newest-first; entries from any show tagged with this topic
      semiconductors.md
  all-in/
    2026-05-22-e212-foo.md               # per-episode markdown: frontmatter + plain-text transcript
  acquired/
    2026-05-20-tsmc.md
```

Per-episode file frontmatter (downstream skill's contract):

```yaml
---
podcast: All-In
episode_title: 'E212: Nvidia earnings, EU AI Act, weekend reads'
date: 2026-05-22
hosts: [Chamath Palihapitiya, Jason Calacanis, David Sacks, David Friedberg]
guests: []
url: https://www.youtube.com/watch?v=...
---
[plain-text transcript here]
```

The episode summary does **not** live in the frontmatter or the body.
It lives one layer up, in the index files (`timeline.md`,
`by-podcast/<slug>.md`, `topics/<slug>.md`), next to the link to the raw
transcript file. This mirrors curunir's existing memory layout
(`src/memory_indexer.py`): `README.md` routes → index files give cheap
browsable summaries with topic tags → raw transcript files are loaded
on demand when verbatim content is needed.

Each index entry looks roughly like:

```markdown
### 2026-05-22 — All-In E212: Nvidia earnings, EU AI Act, weekend reads
**Topics:** ai-policy, semiconductors, markets
- Nvidia Q1 results beat consensus; data-center revenue mix shifts
- EU AI Act enforcement delayed six months after industry pushback
- Hosts debate whether private-credit growth signals systemic risk
[transcript](../all-in/2026-05-22-e212-foo.md)
```

## Config (`podcasts.yaml`)

```yaml
- name: All-In
  slug: all-in
  source: https://www.youtube.com/@allin
  include_pattern: '^E\d+:'
  # exclude_pattern: optional regex to subtract from include matches
- name: Acquired
  slug: acquired
  source: https://www.youtube.com/@AcquiredFM
  # no filter — take everything from this source
```

`source` can be a channel URL or a playlist URL. If the show has a
creator-curated playlist that already contains exactly what the user
wants, point at the playlist and skip the regex. Otherwise filter the
channel feed with `include_pattern` / `exclude_pattern` (Python regex,
matched against the episode title).

## Visibility

The skill is `hidden: true` in its frontmatter. It does not appear in
the system-prompt skill manifest, so the agent will not route to it
based on conversational triggers. It is invoked explicitly:

- By the user via the slash form `/podcast-ingest <mode> [args]`.
- By the scheduler via the `skill:` field in the `context/schedules.json` entry.
- By the agent via `load_skill("podcast-ingest")` if the user references it by name.

No `portal_summary` or `portal_starter` — this skill stays off the
portal entirely.

## Skill modes

The skill is a single `SKILL.md` runbook. The agent picks a mode based
on the invoking message (scheduler prompt or user request):

### Mode A — `pull` (the weekly cron path)

Scheduler invokes with: *"Run the podcast-ingest skill in pull mode."*

1. Read `context/workspace/podcasts/podcasts.yaml`.
2. For each show:
   - Run `yt-dlp --flat-playlist --print '%(id)s\t%(title)s\t%(upload_date)s' --dateafter $(date -u -v-8d +%Y%m%d) <source>` to list videos uploaded in the last 8 days (one-day overlap with the weekly cadence catches stragglers).
   - Drop any video ID already in `.seen-ids.txt`.
   - Apply `include_pattern` / `exclude_pattern` to the title.
3. For each surviving episode, run the per-episode ingest (next section).
4. After all shows are processed, append a one-paragraph report ("Ingested 3 new episodes: …") to the conversation.

Per-show failures (yt-dlp errors, network timeouts) log and continue —
one bad show does not block the others. Per-episode failures inside a
show also continue — one bad episode does not block the rest of the
show.

### Per-episode ingest

1. **Transcript.** Use the exact pipeline from `skills/youtube-transcript/SKILL.md`: `yt-dlp --write-auto-sub --sub-lang en --skip-download --sub-format vtt` → awk strip timestamps and per-word-timed duplicates → `awk '!seen[$0]++'` → `sed` decode entities → write to `/tmp/yt_<id>.txt`. Use the `read` tool to load it (the bash 30k-char output limit would silently truncate).
2. **Metadata.** `yt-dlp --skip-download --print-json <url>` for upload_date, channel name, full title, description.
3. **Hosts + guests.** Single LLM call with the title and description as input, prompted to extract `hosts` (regulars on this show) and `guests` (mentioned in this episode's title/description) as YAML lists. The show's known-hosts list lives in `podcasts.yaml` under an optional `known_hosts:` field once discovered; the first time a show is ingested, the LLM fills it in and the skill writes it back to the config so subsequent episodes can skip the host-extraction step.
4. **Summary + topics.** Single LLM call with the transcript as input, prompted to produce 5–10 bullets and a list of kebab-case topic slugs (drawn from an existing-topics list passed in the prompt so the topic taxonomy stays stable; LLM may propose a new slug if nothing fits). Output goes into the index files, not the per-episode file.
5. **Write the per-episode file.** Frontmatter (the locked schema) + plain-text transcript body. Path: `context/workspace/podcasts/<slug>/<YYYY-MM-DD>-<episode-slug>.md`. Episode slug is the title, lowercased, non-alphanumerics replaced with `-`, truncated to ~60 chars.
6. **Update indexes.** Upsert one entry into `summaries/timeline.md`, one into `summaries/by-podcast/<slug>.md`, and one into each `summaries/topics/<topic>.md` the episode touches. Upsert key is the raw transcript file path — re-ingesting an episode replaces its entry in place. Entries are newest-first within each file.
7. **Mark seen.** Append the YouTube video ID to `.seen-ids.txt`. Only after the raw file is on disk, so a crash before step 5 retries cleanly on the next run.

### Mode B — `add <show name or URL>`

User says *"add the All-In podcast to my podcast feed."* Skill:

1. Resolve the source URL. If the user gave a URL, use it. If they gave a name, use `yt-dlp 'ytsearch5:<name> podcast'` to surface candidate channels, present the top results to the user, and ask which is right.
2. Pull the most recent ~20 videos via `yt-dlp --flat-playlist --print '%(id)s\t%(title)s\t%(upload_date)s' <source> | head -20`.
3. Show the user the titles, ask which they want each week. ("Looking at the last 20: these are numbered weekly recaps `E210:`, `E211:`, `E212:`, mixed with interview titles like `Marc Andreessen on …` and special episodes. Which do you want?")
4. Propose an `include_pattern` (and/or `exclude_pattern`) regex. Run the regex against the 20-episode sample, show which titles it matches and which it excludes, ask the user to confirm or refine.
5. Iterate steps 3–4 until the user signs off.
6. Append the entry to `podcasts.yaml`. Ask if the user wants to backfill (Mode D) and how many episodes.

### Mode C — `list` / `remove <slug>` / `edit <slug>`

- `list`: print the configured shows with their slug, source, and filters.
- `remove <slug>`: delete the entry from `podcasts.yaml`. Ask before deleting the show's directory and index entries; default is to leave them in place.
- `edit <slug>`: re-run Mode B's filter-discovery dialogue against the existing entry, then save the updated entry.

### Mode D — `backfill <slug> [--episodes N]`

Same as Mode A but for one show, with no date cutoff — list the most
recent N (default 10) videos from `<source>`, apply the filter, ingest
all unseen survivors. For first-time setup of a newly added show.

### Mode E — `rebuild-indexes`

Re-derive `summaries/timeline.md`, `summaries/by-podcast/*.md`, and
`summaries/topics/*.md` from the raw transcript files on disk. Read
each per-episode file's frontmatter, regenerate its summary + topics
via LLM (or read the previous entry from the existing index if it
exists and is well-formed), and rewrite the index files. Recovery
path when indexes get out of sync.

## Scheduler entry

Added to `context/schedules.json`:

```json
{
  "id": "podcast-ingest-weekly",
  "cron": "0 8 * * 1",
  "skill": "podcast-ingest",
  "prompt": "Run the podcast-ingest skill in pull mode. Process all shows in podcasts.yaml against the last 8 days of episodes, filter, transcribe, summarize, store, and update indexes. Return a one-paragraph report.",
  "enabled": true
}
```

Monday 8am local. One overlap day on either side of "weekly" so an
episode dropped on the wrong day of the week doesn't fall through the
cracks; the `.seen-ids.txt` ledger handles deduping.

## Tool surface

All existing tools — no new code needed:

- `bash` for `yt-dlp` invocations (discovery, transcript download,
  metadata).
- `read` for loading the transcript file (bypassing the bash output
  truncation).
- `write` and `edit` for the per-episode files, `podcasts.yaml`,
  `.seen-ids.txt`, and the index files.
- LLM (via the agent loop itself) for host/guest extraction, summary
  generation, and topic tagging.
- `load_skill` to reach `youtube-transcript`'s transcript pipeline.

## Failure modes and recovery

- **yt-dlp fails on a show:** log the error, skip the show, continue. Show is retried next week.
- **yt-dlp fails on one episode within a show:** log, skip the episode, continue. Episode is **not** added to `.seen-ids.txt` and is retried next week.
- **LLM call fails mid-episode (host extraction or summary):** the per-episode file is not written, the ID is not added to `.seen-ids.txt`, the episode is retried next week.
- **Crash between writing the per-episode file and appending to `.seen-ids.txt`:** next run re-ingests the episode and overwrites the file. The index update is idempotent (upsert by path), so no duplication.
- **Indexes drift out of sync with raw files** (e.g. user manually deletes a transcript, or an upsert bug double-writes an entry): run Mode E to rebuild from disk.
- **Regex matches the wrong episodes:** user runs Mode C `edit <slug>` to fix; the misingested episodes can be manually deleted, indexes rebuilt with Mode E.

## Testing

Manual end-to-end testing only for v1. The skill is a runbook, not
Python code, so the existing pytest suite doesn't apply. The validation
loop is:

1. Add a show via Mode B against a real channel; check the proposed
   regex matches the right titles in the sample.
2. Run Mode D to backfill 3 episodes; inspect that the per-episode
   files match the locked frontmatter schema and the indexes have
   correct entries.
3. Enable the scheduler entry; one week later, check that new
   episodes were picked up, old ones were skipped via the ledger,
   and the report came through.

If the user's downstream consumer skill surfaces shape mismatches, they
get fixed in this skill and the user re-runs Mode E.

## Open questions

None at draft time.
