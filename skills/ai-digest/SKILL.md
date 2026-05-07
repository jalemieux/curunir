---
name: ai-digest
description: "Use when the user (or scheduler) asks for a daily AI news digest. Produces a short briefing of fresh AI/ML stories from the past 24 hours, gated by a verification ledger that rejects any item older than 1 day or already shipped in the past 7 days."
---

# AI Digest

Produce a short briefing of fresh AI/ML stories from the last 24 hours.

## Why these steps exist (read this first)

This skill has shipped stale items **three times**: once in April, once mid-April,
and again on 2026-05-05 (the May 5 digest leaked a 19-day-old Opus 4.7 release,
an 18-day-old Anthropic Labs design post, and a same-day-old Anthropic enterprise
announcement). All three failures came from a single cause: the freshness rule
was prose, the agent was free to skip it, and it did.

Every step below is load-bearing. Do not collapse, summarize, or skip them.
The verification ledger and the URL blacklist are **structural** — if the
artifact isn't on disk, the item cannot ship. If you find yourself wanting to
ship an item that didn't pass verification, the correct response is a shorter
digest, not a relaxed rule.

## Inputs

- **Digest date** — today, in `YYYY-MM-DD`. Use `date -u +%F`.
- **Cadence** — daily by default. If the user specifies weekly, swap `freshness=pd` for `freshness=pw` in step 1 and the AGE_DAYS cap in step 2 (1 → 7).

## Step 1 — Search with API-level freshness filter

Run a small set of focused Brave queries with `freshness=pd` (past day). This
removes most stale candidates at the API boundary. `pd` is not authoritative
(Brave occasionally surfaces re-indexed older content), so the verification in
step 2 is still required — `freshness` is a cheap pre-filter, not a guarantee.

Recommended queries (adjust based on what's been shipped recently — see step 3):

```bash
DIGEST_DATE=$(date -u +%F)
mkdir -p /tmp/ai-digest
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
done > /tmp/ai-digest/candidates.jsonl
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
shipped in the past 7 days:

```bash
# Reject anything sent in the past 7 days
SEVEN_DAYS_AGO=$(date -u -v-7d +%F 2>/dev/null || date -u -d '7 days ago' +%F)
while read url; do
  # Lines look like: 2026-05-04 https://example.com/article
  hit=$(grep -F "$url" context/memory/ai-digest-sent.md || true)
  if [ -n "$hit" ]; then
    last_date=$(echo "$hit" | tail -1 | awk '{print $1}')
    if [ "$last_date" \> "$SEVEN_DAYS_AGO" ] || [ "$last_date" = "$SEVEN_DAYS_AGO" ]; then
      echo "DEDUP: $url (last sent $last_date)"
    fi
  fi
done < /tmp/ai-digest/keep-urls.txt
```

Update the ledger: any URL flagged DEDUP becomes `REJECT: dedup`.

## Step 4 — Compose the digest

Items eligible for the digest = ledger rows where `DECISION = KEEP`.

- **3+ eligible items** → normal digest. One short paragraph per item, with the
  source URL and publication date inline.
- **1–2 eligible items** → "Light news day" digest with whatever passed.
  Header: `# AI Digest — {DIGEST_DATE} (light news day)`.
- **0 eligible items** → ship a one-line message: `# AI Digest — {DIGEST_DATE}\n\nNo fresh stories passed verification today.` Do not reach back further. Do not lower the bar.

Including a stale item to "round out" the digest is the failure mode this
skill exists to prevent. A short digest is the correct output, not a problem to
solve.

## Step 5 — Record shipped URLs

Append every URL that made it into the digest to
`context/memory/ai-digest-sent.md`, one per line, ISO-date prefixed:

```bash
for url in <shipped_urls>; do
  echo "$DIGEST_DATE $url" >> context/memory/ai-digest-sent.md
done
```

The append-only ledger is what step 3 reads next time. If the file doesn't
exist yet, create it with the header `# AI digest URL ledger — one ISO-date + URL per line`.

## Output checklist

Before sending the digest, confirm in your reasoning:

- [ ] Verification ledger table is in scratch with one row per candidate.
- [ ] Every shipped item has `DECISION = KEEP` in the ledger.
- [ ] Every shipped item has `PUBLISHED ≠ UNKNOWN` and `AGE_DAYS ≤ 1`.
- [ ] Step 3 dedup ran against `context/memory/ai-digest-sent.md`.
- [ ] Step 5 appended shipped URLs to the ledger.

If any box is empty, do not send the digest. Fix the gap or ship a shorter
digest instead.
