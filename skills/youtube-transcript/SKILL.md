---
name: youtube-transcript
description: "Use whenever you need the spoken contents of a YouTube video — to read, review, summarize, quote, fact-check, or analyze what was discussed. Trigger phrases include 'transcript of this YouTube video', 'what was discussed in this YT video', 'read/review/summarize this YouTube link', 'what does this video say about X', or any YouTube URL where the actual words matter (not just metadata or thumbnail). Goes straight to yt-dlp — do not try YouTube's timedtext endpoint, third-party scrapers, or page-scraping."
---

# YouTube Transcript

Fetch the auto-generated (or human-uploaded) captions for a YouTube video as plain text, using `yt-dlp`. One command, no scraping.

## Why this skill exists

The YouTube `timedtext` endpoint, `youtubetranscript.com`, `kome.ai`, and scraping `ytInitialPlayerResponse` from the watch page all fail unpredictably from server IPs (signature expiry, empty bodies, 403s). `yt-dlp` handles all of that internally and is the only reliable path. **Use it first. Do not try the others.**

## Usage

```bash
# Fetch auto-generated English captions, no video download
yt-dlp --write-auto-sub --sub-lang en --skip-download \
       --sub-format vtt \
       --output "/tmp/yt_%(id)s" \
       "https://youtu.be/VIDEO_ID"
# → writes /tmp/yt_VIDEO_ID.en.vtt
```

Then strip VTT timestamps, inline word-timing tags, and entities — **and write to a file**, do not pipe to stdout (see warning below):

```bash
awk '
  /^WEBVTT/ || /^Kind:/ || /^Language:/ {next}
  /^[0-9]+$/ {next}              # cue numbers
  /-->/ {next}                   # cue timestamps
  /^$/ {next}                    # blank lines
  /<[0-9:.]+>/ {next}            # per-word-timed duplicate lines
  {print}
' /tmp/yt_VIDEO_ID.en.vtt \
  | awk '!seen[$0]++' \
  | sed 's/&gt;/>/g; s/&lt;/</g; s/&amp;/\&/g; s/&#39;/'"'"'/g' \
  > /tmp/yt_VIDEO_ID.txt

# Print only stats to bash output, not the transcript itself
wc -l -w -c /tmp/yt_VIDEO_ID.txt
```

Then use the **`read` tool** to load the transcript into context:

```
read(file_path="/tmp/yt_VIDEO_ID.txt")
```

What each step does:

- `/<[0-9:.]+>/ {next}` — auto-captions ship two parallel versions of each line: a clean one and one with per-word timing tags (`<00:00:01.120><c>back</c>`). Drop the tagged duplicates.
- `awk '!seen[$0]++'` — dedupes the scrolling-caption repeats (each phrase appears 2-3 times as it slides up the screen).
- `sed` at the end — decodes the HTML entities VTT uses for `>`, `<`, `&`, `'`. Speaker-change markers (`>>`) come through correctly after this.

### ⚠️ Critical: never pipe the transcript through bash stdout

The `bash` tool **truncates output at 30,000 chars** (~5,000 words). A typical podcast transcript is 15-40k words. If you `cat` the transcript or pipe the awk pipeline straight into bash output, the agent silently sees only the first ~15-20% of the file and confidently summarizes a partial transcript.

Always: pipeline → file → `read` tool. Use bash only for the `yt-dlp` download, the awk transform-to-file, and `wc` for stats.

## Variants

**Human-uploaded subtitles (preferred when available):**

```bash
yt-dlp --write-sub --sub-lang en --skip-download \
       --sub-format vtt --output "/tmp/yt_%(id)s" \
       "https://youtu.be/VIDEO_ID"
```

Use `--write-sub --write-auto-sub` together to grab human subs if present and fall back to auto.

**Non-English videos:**

```bash
# List available languages first
yt-dlp --list-subs "https://youtu.be/VIDEO_ID"

# Then specify
yt-dlp --write-auto-sub --sub-lang fr --skip-download ...
```

**Translated to English:**

```bash
# yt-dlp can request YouTube to translate auto-captions
yt-dlp --write-auto-sub --sub-lang en-orig --skip-download ...
# or for a specific source language
yt-dlp --write-auto-sub --sub-lang "en.*" --skip-download ...
```

## Output Formats

| Format | Flag | Notes |
|--------|------|-------|
| VTT | `--sub-format vtt` | Default. Easy to strip with awk. |
| SRT | `--sub-format srt` | Numbered blocks + timestamps. Also easy to parse. |
| TTML | `--sub-format ttml` | XML; parse with `xml.etree`. Use only if you need word-level timing. |
| JSON3 | `--sub-format json3` | Word-level timing + segment IDs. Use for highlighting/seeking. |

For pure text output, **stick with VTT**.

## Tips

- Always run `wc -w` against the cleaned `.txt` file first so you know how much you're about to load — long-form videos (podcasts, lectures) can yield 30k+ words.
- The output filename uses yt-dlp's template — `%(id)s` is the video ID, so re-runs overwrite cleanly in `/tmp/`.
- For batch processing, pass a playlist URL — yt-dlp writes one subtitle file per video.
- If the video has no captions at all, yt-dlp prints `WARNING: There are no subtitles for the requested languages` and exits 0. Check that the `.vtt` file exists before parsing.
- For very long transcripts (>50k words / ~300KB), read in chunks with the `read` tool's `offset`/`limit` params, or grep for sections of interest before reading.

## Common Mistakes

- **Piping the transcript through bash stdout** — the bash tool truncates output at 30,000 chars (~5k words). A typical podcast is 15-40k words. The agent gets the first 15-20% and confidently summarizes a partial transcript without realizing. Always write to a file and use the `read` tool. See the warning under Usage.
- **Trying YouTube's `timedtext` endpoint or third-party transcript services first** — they fail unpredictably from server IPs. `yt-dlp` is the only reliable path. Go to it directly.
- **Forgetting `--skip-download`** — without it, yt-dlp downloads the full video, which is slow and wastes disk.
- **Not deduping VTT output** — auto-captions scroll, so each phrase appears 2-3 times in the raw text. Always dedupe with `awk '!seen[$0]++'`.
- **Forgetting to strip inline word-timing tags** — auto-captions emit each line twice: a clean version and one with per-word `<00:00:01.120><c>word</c>` tags. Filter the tagged lines (`/<[0-9:.]+>/ {next}`) or you'll get garbled output.
- **Leaving HTML entities in the output** — VTT uses `&gt;&gt;` for speaker changes and escapes `<`, `&`, `'`. Decode them at the end of the pipeline or downstream LLM calls see noise.
- **Hardcoding `--sub-lang en`** for videos in other languages — list subs first with `--list-subs` if the language isn't obvious from context.
- **Parsing VTT with regex byte-by-byte** — awk filters by line type (timestamp, blank, text) are simpler and more robust.
