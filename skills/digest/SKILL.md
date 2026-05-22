---
name: digest
description: "Use when the user (or scheduler) asks for a recurring news digest on any topic — AI/ML, personal finance, local news, a specific company. Produces a short briefing of fresh stories from a given window, gated by a verification ledger that rejects any item older than the cadence cap or already shipped recently, plus a per-topic URL dedup ledger."
---

# Digest

Produce a short briefing of fresh stories on a given topic from a recent window.

## Why these steps exist (read this first)

This skill (as the topic-specific `ai-digest` it was generalized from) has shipped
stale items **three times**: once in April, once mid-April, and again on 2026-05-05
(the May 5 digest leaked a 19-day-old Opus 4.7 release, an 18-day-old Anthropic Labs
design post, and a same-day-old Anthropic enterprise announcement). All three
failures came from a single cause: the freshness rule was prose, the agent was free
to skip it, and it did.

Every step below is load-bearing. Do not collapse, summarize, or skip them.
The verification ledger and the URL blacklist are **structural** — if the
artifact isn't on disk, the item cannot ship. If you find yourself wanting to
ship an item that didn't pass verification, the correct response is a shorter
digest, not a relaxed rule. None of this is topic-specific: the safeguards apply
to a finance digest or a local-news digest exactly as they do to an AI one.

## Inputs

This skill is loaded as prompt content — there are no structured arguments. The
caller supplies these in their message or the scheduler `prompt` field, the same
way *Digest date* and *Cadence* are supplied below.

- **Topic** *(required)* — the subject of the digest, free text, e.g. `AI/ML`,
  `personal finance`, `San Francisco local news`, `Anthropic`. Drives the search
  queries, the digest header, and the ledger path.
- **Search queries** *(optional)* — an explicit set of search strings. If omitted,
  derive a small, focused query set (3–6 queries) from the Topic. Quality depends
  heavily on good queries, so for a narrow or unusual Topic the caller should pass
  these explicitly. The AI/ML set in step 1 is kept as a worked example.
- **Cadence** *(optional, default daily)* — daily or weekly. Daily uses
  `freshness=pd` in step 1 and an `AGE_DAYS` cap of 1 in step 2. Weekly uses
  `freshness=pw` and a cap of 7.
- **Ledger path** *(optional)* — the dedup ledger file. Default
  `context/memory/digest-<topic-slug>-sent.md`. The **topic-slug** is derived
  deterministically: lowercase the Topic, replace every run of non-alphanumeric
  characters with a single `-`, and strip leading/trailing `-`. So `AI/ML` →
  `ai-ml`, `personal finance` → `personal-finance`. Pass an explicit Ledger path
  to pin a file independent of the Topic text (e.g. the bundled AI/ML schedule
  entry pins `context/memory/digest-ai-sent.md`). First-run note: if you are
  migrating from the retired `ai-digest` skill, copy any existing
  `context/memory/ai-digest-sent.md` to the new Ledger path so recent-send
  history is not lost.
- **Digest date** — today, in `YYYY-MM-DD`. Use `date -u +%F`.

Throughout the steps below, `<slug>` is the topic-slug and `{Topic}` is the
Topic text.

## Step 1 — Search with API-level freshness filter

Run a small set of focused Brave queries with `freshness=pd` (past day; use `pw`
for weekly cadence). This removes most stale candidates at the API boundary. `pd`
is not authoritative (Brave occasionally surfaces re-indexed older content), so the
verification in step 2 is still required — `freshness` is a cheap pre-filter, not a
guarantee.

Use the **Search queries** input if one was given. Otherwise derive 3–6 focused
queries from the Topic. The block below is the worked example for `Topic: AI/ML`
— substitute your own topic and queries:

```bash
DIGEST_DATE=$(date -u +%F)
mkdir -p /tmp/digest-<slug>
for q in \
  "AI announcement" \
  "LLM release" \
  "AI research paper" \
  "AI policy regulation" \
  "AI funding round"; do
  curl -s "https://api.search.brave.com/res/v1/web/search?q=$(printf %s "$q" | jq -sRr @uri)&count=10&freshness=pd" \
    -H "Accept: application/json" \
    -H "X-Subscription-Token: $BRAVE_API_KEY" \
    | jq '.web.results[] | {title, url, description, page_age}'
done > /tmp/digest-<slug>/candidates.jsonl
```

Cap the candidate set at **12 URLs** before moving to step 2. Running
`web_fetch` on every result is wasteful and risks context overflow. Pick the 12
most newsworthy candidates by title/description.

## Step 2 — Verification ledger (mandatory)

For **each** of the (up to 12) candidate URLs, call `web_fetch` with the
extraction prompt below and record the result in a markdown ledger. The ledger
is the artifact — if it doesn't exist for an item, the item cannot ship.

```
web_fetch(
  url=<candidate_url>,
  prompt="Extract the publication date in ISO format (YYYY-MM-DD) and the article title. Return ONLY two lines: PUBLISHED: <YYYY-MM-DD or UNKNOWN>\nTITLE: <title>. If no publication date is shown on the page, return PUBLISHED: UNKNOWN."
)
```

If `web_fetch` returns `PUBLISHED: UNKNOWN`, retry once via the `playwright`
skill (`shot-scraper javascript ... 'document.querySelector(...).content'` to
read JSON-LD or `<meta>` date tags). If still UNKNOWN after the retry, **reject
the item**.

Build the ledger as a markdown table to scratch space (e.g., write it as part
of your reasoning before composing the digest):

| URL | PUBLISHED | TITLE | AGE_DAYS | DECISION |
|-----|-----------|-------|----------|----------|

Where:
- `AGE_DAYS` = `DIGEST_DATE - PUBLISHED` in days (calendar days, UTC).
- `DECISION` is `KEEP` only if `PUBLISHED ≠ UNKNOWN` **and** `AGE_DAYS ≤ 1` (or
  `≤ 7` for weekly cadence). Anything else is `REJECT: <reason>`.

**Hard rule, no exceptions:** an item with `AGE_DAYS > 1` (daily) or
`PUBLISHED: UNKNOWN` is `REJECT`. Do not relax this because Brave returned only
a few hits, because a story is "obviously big news," or because you remember
the date from training data. If it isn't on the page, it doesn't ship.

## Step 3 — Dedup against the recent-sends ledger

For each URL still marked `KEEP`, check whether it (or a near-equivalent) was
shipped in the past 7 days. `LEDGER` is the **Ledger path** input (default
`context/memory/digest-<slug>-sent.md`):

```bash
# Reject anything sent in the past 7 days
LEDGER=context/memory/digest-<slug>-sent.md
SEVEN_DAYS_AGO=$(date -u -v-7d +%F 2>/dev/null || date -u -d '7 days ago' +%F)
while read url; do
  # Lines look like: 2026-05-04 https://example.com/article
  hit=$(grep -F "$url" "$LEDGER" || true)
  if [ -n "$hit" ]; then
    last_date=$(echo "$hit" | tail -1 | awk '{print $1}')
    if [ "$last_date" \> "$SEVEN_DAYS_AGO" ] || [ "$last_date" = "$SEVEN_DAYS_AGO" ]; then
      echo "DEDUP: $url (last sent $last_date)"
    fi
  fi
done < /tmp/digest-<slug>/keep-urls.txt
```

Update the ledger: any URL flagged DEDUP becomes `REJECT: dedup`.

## Step 4 — Compose the digest

Items eligible for the digest = ledger rows where `DECISION = KEEP`.

- **3+ eligible items** → normal digest. Header: `# {Topic} Digest — {DIGEST_DATE}`.
  One short paragraph per item, with the source URL and publication date inline.
- **1–2 eligible items** → "Light news day" digest with whatever passed.
  Header: `# {Topic} Digest — {DIGEST_DATE} (light news day)`.
- **0 eligible items** → ship a one-line message:
  `# {Topic} Digest — {DIGEST_DATE}\n\nNo fresh stories passed verification today.`
  Do not reach back further. Do not lower the bar.

Including a stale item to "round out" the digest is the failure mode this
skill exists to prevent. A short digest is the correct output, not a problem to
solve.

## Step 5 — Record shipped URLs

Append every URL that made it into the digest to the **Ledger path**, one per
line, ISO-date prefixed:

```bash
LEDGER=context/memory/digest-<slug>-sent.md
for url in <shipped_urls>; do
  echo "$DIGEST_DATE $url" >> "$LEDGER"
done
```

The append-only ledger is what step 3 reads next time. If the file doesn't
exist yet, create it with the header `# digest URL ledger — one ISO-date + URL per line`.

## Step 6 — Deliver

The digest is delivered inline — **never as a PDF, never as an attachment**.
The markdown produced in step 4 is the deliverable.

- **Chat session** — send the markdown as the final assistant message. Done.
- **Email** — load the `email-send` skill and send with **both** `text_body`
  (the raw markdown) **and** `html_body` (the markdown rendered to HTML). The
  `html_body` is mandatory — do not send a text-only email, and do not attach
  a PDF or any other file. Render with Python's `markdown` library:

  ```python
  import markdown
  html_body = markdown.markdown(digest_md, extensions=["extra", "sane_lists"])
  ```

  Wrap it in a minimal HTML document so links and headings render cleanly in
  every mail client:

  ```python
  html_body = f"""<!DOCTYPE html>
  <html><body style="font-family: -apple-system, system-ui, sans-serif; max-width: 680px; margin: 0 auto; padding: 16px; line-height: 1.5;">
  {html_body}
  </body></html>"""
  ```

  If `markdown` isn't installed, fall back to `pip install --quiet markdown`
  in the same shell before the render. Do not ship a digest as HTML-escaped
  markdown — that defeats the purpose.

If a run is ever asked to save the digest to disk, write it to
`workspace/generated/` — but disk output is in addition to inline delivery,
not a replacement for it, and it is still markdown (not PDF).

## Output checklist

Before sending the digest, confirm in your reasoning:

- [ ] Verification ledger table is in scratch with one row per candidate.
- [ ] Every shipped item has `DECISION = KEEP` in the ledger.
- [ ] Every shipped item has `PUBLISHED ≠ UNKNOWN` and `AGE_DAYS ≤ 1`.
- [ ] Step 3 dedup ran against the per-topic Ledger path.
- [ ] Step 5 appended shipped URLs to the ledger.
- [ ] If delivering by email, both `text_body` and `html_body` are set, no attachments, no PDF.

If any box is empty, do not send the digest. Fix the gap or ship a shorter
digest instead.
