---
name: digest
description: "Use when the user (or scheduler) asks for a recurring news digest on any topic — AI/ML, personal finance, local news, a specific company. Produces a short briefing of fresh stories from a given window, gated by a verification ledger that rejects any item older than the cadence cap or already shipped recently, plus a per-topic URL dedup ledger."
portal_summary: "Short news briefing on any topic — AI, finance, a company, or a city"
portal_starter: true
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
- **1–2 eligible items** → "Light news day" digest with whatever passed.
  Header: `# {Topic} Digest — {DIGEST_DATE} (light news day)`.
- **0 eligible items** → ship a one-line message:
  `# {Topic} Digest — {DIGEST_DATE}\n\nNo fresh stories passed verification today.`
  Do not reach back further. Do not lower the bar.

Including a stale item to "round out" the digest is the failure mode this
skill exists to prevent. A short digest is the correct output, not a problem to
solve.

### Per-item template

Each item is **one paragraph** in this exact shape:

```
**{Lead headline as a sentence — what happened, in active voice}.** {1–3 sentences of supporting detail: who, how big, why it matters, follow-on consequence.} ([{Publisher}]({url}), {YYYY-MM-DD})
```

Hard rules — these are the rules the model breaks first, so re-check them in
the output checklist:

- **No subheadings between items.** No `##` per story, no bullets, no numbered
  list, no "Read more →" links. Items are separated by a single blank line.
- **Citation goes at the end in parentheses**, and the publisher name is the
  hyperlink: `([Bloomberg](https://...), 2026-05-24)`. Do not paste the bare
  URL. Do not put the citation on its own line.
- **Publisher name** is the human-readable brand derived from the URL hostname
  — `bloomberg.com` → "Bloomberg", `techcrunch.com` → "TechCrunch",
  `pymnts.com` → "PYMNTS". Not the hostname itself, not the article title
  prefix. If the article is syndicated (e.g. via `news.google.com`), use the
  original outlet named on the page, not the syndicator.
- **Lead sentence is bolded** with `**…**` and ends in a period before the
  detail sentences begin.

### Canonical example (this is what good looks like)

```markdown
# AI/ML Digest — 2026-05-25

**Anthropic targets $900B valuation in record funding round.** Bloomberg reports Anthropic is in talks to raise at least $30 billion at a valuation above $900 billion, potentially closing as soon as this week. The deal would vault it past OpenAI as the world's most valuable private AI startup. Quarterly revenue is projected to more than double to $10.9B in Q2, with an annualized run rate topping $50B by end of June — driven largely by Claude Code, which has become one of the fastest-scaling enterprise software products on record. Sequoia, Dragoneer, Altimeter, and Greenoaks are expected to co-lead. ([PYMNTS](https://www.pymnts.com/...), 2026-05-24)

**White House shelves AI safety executive order at the last minute.** President Trump postponed signing a draft AI safety executive order on May 21, telling reporters he "didn't like certain aspects" and worried it could slow the US lead over China. The draft would have created a voluntary 90-day review process for new AI models before release. ([Asanify](https://www.asanify.com/...), 2026-05-25)

**Illinois Senate advances frontier AI model regulation bill.** The Illinois Senate voted 52-5 to pass SB 315, which would require large AI developers (>$500M revenue) to adopt transparency frameworks, undergo third-party audits, and report catastrophic risk capabilities. Modeled after similar laws in California and New York, the bill has support from OpenAI and Anthropic. The effective date was amended to 2028. ([KWQC](https://www.kwqc.com/...), 2026-05-25)
```

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
- **Email** — load the `email-send` skill and send the digest markdown as the
  `--body`/`--body-file`. The CLI renders that markdown into styled HTML
  automatically and sends both parts, so the digest displays as a clean
  newspaper-brief in every mail client — you do **not** render HTML or pass
  `--html-file` yourself (it's only for custom styling). Do not send a text-only
  email by stripping markdown, and do not attach a PDF or any other file.

If a run is ever asked to save the digest to disk, write it to
`context/workspace/generated/` — but disk output is in addition to inline delivery,
not a replacement for it, and it is still markdown (not PDF).

## Output checklist

Before sending the digest, confirm in your reasoning:

- [ ] Verification ledger table is in scratch with one row per candidate.
- [ ] Every shipped item has `DECISION = KEEP` in the ledger.
- [ ] Every shipped item has `PUBLISHED ≠ UNKNOWN` and `AGE_DAYS ≤ 1`.
- [ ] Step 3 dedup ran against the per-topic Ledger path.
- [ ] Step 5 appended shipped URLs to the ledger.
- [ ] Each shipped item starts with a bolded lead sentence and ends with a `([Publisher](url), YYYY-MM-DD)` citation — no subheadings, bullets, or numbering between items.
- [ ] If delivering by email, the digest markdown is sent as the body (the CLI auto-renders HTML), no attachments, no PDF.

If any box is empty, do not send the digest. Fix the gap or ship a shorter
digest instead.
