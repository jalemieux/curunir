---
name: introspect
description: "Use to review curunir's own logs (the rotating $LOG_FILE, or docker logs) for regressions, errors, loops, tool-misuse, or context overflows. Trigger on a schedule (e.g. hourly via the `schedule` tool) — files GitHub issues for novel findings, deduping against open ones — or on demand when the user asks to scan logs / check on recent behavior ('check the logs', 'how has the agent been behaving', 'any errors lately'), in which case it just reports findings back in chat instead of filing issues."
tools: bash
---

# Introspect

Self-hosted observability loop. Scan recent logs, classify findings, then
either report them back to the user or file GitHub issues for novel problems.

The scan (Steps 1–3) is always the same. The only thing that changes is what
you do with the findings:

- **On a schedule / when asked to "file issues"** — dedup against open issues
  (Step 4), file novel ones (Step 5), close with a ledger line (Step 6).
- **When a user asks in chat** to scan logs or check on recent behavior — just
  report what you found back to them (Step A) and stop. **Do not file GitHub
  issues unless they explicitly ask** ("...and open issues for anything new") —
  silently filing tickets in response to a question is the surprising part, so
  never do it unprompted. GitHub auth, dedup, and the ledger are all skippable.

**Requires:**
- A readable log source — either the rotating log file at `$LOG_FILE`
  (default `/app/workspace/curunir.log`, written by `run.py` and persisted on
  the workspace volume), or `docker logs` access (host `docker` CLI, or
  `/var/run/docker.sock` mounted in alongside a `docker` client)

**Scheduled mode also requires** `gh` CLI authenticated via `GH_TOKEN` (see the
`github` skill for details) to dedup and file issues. Ad-hoc mode does not — it
reports findings in chat and never touches GitHub.

When running inside the curunir container, `$LOG_FILE` is the normal path —
`docker logs` is not reachable without the socket mounted. If neither source
is available: in scheduled mode, log a one-line failure to
`context/memory/introspection.md` and exit cleanly; in ad-hoc mode, tell the
user the log source is unreachable. Do not invent findings either way.

## Inputs

- **Window** — how far back to scan. Default `1h`. Override via the prompt.
- **Log source** — where to read logs from. Resolution order:
  1. **`$LOG_FILE`** (default `/app/workspace/curunir.log`) — if it exists and
     is non-empty, prefer it. This is the in-container path. Lines look like
     `2026-05-12 08:30:01 INFO src.agent.agent: ...`. The file rotates at
     10 MB × 3 backups (`curunir.log`, `curunir.log.1`, …), so a wide window
     may need the rotated files too (oldest first).
  2. **`docker logs {container}`** — fallback, for runs that have Docker access
     (typically a host-side curunir). Resolve the container:
     1. `$CURUNIR_CONTAINER` env var
     2. `hostname` self-lookup if inside the container (returns the container ID, which `docker logs` accepts)
     3. `docker ps --filter "name=curunir" --format "{{.Names}}" | head -1`
- **Repo** — where to file findings. Resolution order:
  1. `$INTROSPECT_REPO` env var (`owner/name`)
  2. `gh repo view --json nameWithOwner -q .nameWithOwner` from the curunir clone
  3. Skip filing and surface a clear error in the ledger

## Workflow

### Step 1: Pull logs

Resolve the log source (see **Inputs**), then collect the window into a temp
file — never read raw logs straight into your context.

**Preferred — log file (`$LOG_FILE`, default `/app/workspace/curunir.log`):**

```bash
LOG_FILE="${LOG_FILE:-/app/workspace/curunir.log}"
# Cutoff timestamp matching run.py's "%Y-%m-%d %H:%M:%S" format. Translate the
# window to a form `date` understands — GNU: "1 hour ago" / "24 hours ago";
# BSD/macOS: -v-1H / -v-24H. (Container is Linux → the GNU form.)
SINCE_TS=$(date -u -d "{window} ago" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
        || date -u -v-{window} "+%Y-%m-%d %H:%M:%S")
# Concatenate rotated files oldest-first so timestamps stay ordered, then keep
# lines at or after the cutoff. The 19-char stamp is a fixed-width line prefix,
# so a lexicographic compare on substr($0,1,19) is correct. Untimestamped
# continuation lines (tracebacks) sort before the cutoff and get dropped — the
# pattern scan in Step 2 still catches "Traceback"/"ERROR" header lines, which
# are timestamped, so findings aren't lost.
ls -1tr "$LOG_FILE".* "$LOG_FILE" 2>/dev/null \
  | xargs cat 2>/dev/null \
  | awk -v since="$SINCE_TS" 'substr($0,1,19) >= since' \
  > /tmp/introspect-logs.txt
wc -l /tmp/introspect-logs.txt
```

If you need the full context around a windowed finding (e.g. a multi-line
traceback that got partially trimmed), re-grep the un-windowed `$LOG_FILE` for
the matching session/timestamp rather than widening the whole scan.

**Fallback — `docker logs` (host-side runs only):**

```bash
docker logs --since={window} {container} > /tmp/introspect-logs.txt 2>&1
wc -l /tmp/introspect-logs.txt
```

If `$LOG_FILE` is missing/empty and `docker logs` errors (or isn't available),
log the failure to the ledger and exit. If `$LOG_FILE` exists but the windowed
slice is empty, that's a clean run — emit the `clean` ledger line (Step 6).

### Step 2: Pattern scan (regex)

Each match yields a candidate finding with a category and a signature.

```bash
grep -nE 'ERROR|Traceback|LLM returned empty response|context window exceeded|iteration limit reached|tool_call_failed' /tmp/introspect-logs.txt
```

Suggested categories (one label per finding, prefix `introspect:`):

| Pattern | Category |
|---|---|
| `context window exceeded`, `prompt is too long` | `context-overflow` |
| `iteration limit reached`, `max iterations` | `loop` |
| `LLM returned empty response`, `litellm.*Error` | `llm-error` |
| `tool_call_failed`, repeated `tool error` | `tool-error` |
| Bare `ERROR` / `Traceback` not covered above | `error` |

For each match, build a **signature**: `category` + a normalized fingerprint of
the first line of the stack/error (strip timestamps, paths under `/app/`,
session IDs, hex IDs). The signature is what dedup keys on — it must be stable
across runs.

### Step 3: Behavioral scan (LLM judgment)

Pattern-grep catches hard failures. Behavioral problems need judgment.

Chunk the log file by session boundaries (lines containing
`session_id=` or `[session ...]`). Cap each chunk at ~30k chars; process
chunks one at a time so a chatty hour does not blow the context window.

For each chunk, look for:

- **Loops**: same tool name + identical-or-near-identical args invoked ≥3× within
  one turn. Category: `loop`.
- **Tool mismatch**: the agent wrestled a tool into a job a different tool was
  built for. Concrete example: chains of `bash realpath` + `glob` to resolve a
  user-mentioned file when `attach` would have answered in one call (see PR
  e8ddab3, which tightened `attach`'s description for exactly this reason).
  Category: `tool-mismatch`.
- **Long read chains without progress**: ≥5 `read` calls in a turn with no
  subsequent `edit`/`write`/answer to the user — usually means the agent got
  lost. Category: `read-thrash`.
- **Repeated user clarification**: agent asked the user to repeat themselves or
  re-asked the same question across turns. Category: `clarification-loop`.

**Confidence threshold**: only file a finding if confidence is **high**. If
you would hedge ("might be", "possibly"), drop it. False positives spam the
tracker and erode trust in the loop. (When reporting to a user instead of
filing, you can surface a hedged observation as long as you flag it as
low-confidence.)

---

**If a user asked you to check the logs, stop here.** Summarize the findings
from Steps 2–3 back to them and skip Steps 4–6 (unless they asked you to file
issues). Lead with the verdict — clean, or N findings by category — then for
each category give a one-line description, the count, and the single most
representative excerpt (a few lines, not 30). Note the window and log source so
they know the scope. If something's worth a tracked issue, offer, but only file
it if they say yes. A clean window: say so plainly ("No errors, loops, or
tool-misuse in the last {window}").

The remaining steps are the scheduled / file-issues path.

### Step 4: Dedup

Before filing anything, list current open introspect issues:

```bash
gh issue list --repo {repo} --state open \
  --search "label:introspect in:title,body" \
  --json number,title,labels,body --limit 100
```

For each candidate finding:

1. Compute its signature (Step 2 fingerprint, or for behavioral findings: category + canonical phrase like `loop:bash:ls /tmp`).
2. Search the open issues for that signature. The signature should appear in
   the issue body inside a `<!-- introspect-sig: {sig} -->` HTML comment so it's
   greppable but not user-visible.
3. **If matched** — apply the timestamp guard below before commenting.
4. **If novel** — file a new issue (Step 5).

**Timestamp guard (prevents replay-spam on a fixed bug).** A scheduled rescan
will keep finding the same in-window log lines until the window rolls past
them; without a guard, every tick appends a "Re-occurred" comment for an event
that has not actually re-occurred. Skip the comment when the new finding is not
strictly newer than what's already recorded on the issue.

```bash
# `latest` is the most recent log timestamp already cited on the issue —
# scan fenced excerpts only (the lines between ``` markers) so a human-written
# date in prose can't be mistaken for a real log timestamp.
latest=$(gh issue view {num} --repo {repo} --json body,comments \
    --jq '.body + "\n" + ([.comments[].body] | join("\n"))' \
  | awk '/^```/{f=!f; next} f' \
  | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}' \
  | sort -u | tail -1)
# `current` is the new finding's log timestamp (the 19-char prefix on its
# matched line in /tmp/introspect-logs.txt), NOT $(date) — the wallclock at
# scan time is irrelevant; we're comparing log-event times.
if [ -n "$latest" ] && [ "$current" \> "$latest" ]; then
  gh issue comment {num} --repo {repo} --body "Re-occurred at ${current}. Session: {session_id}. Excerpt:\n\n\`\`\`\n{trimmed_log}\n\`\`\`"
  ledger_action=dup
else
  # Same event the issue already records — don't comment.
  ledger_action=dup-stale
fi
```

The `dup-stale` action goes to the ledger (Step 6) so the run still leaves a
trail; just no GitHub noise.

### Step 5: File novel findings

Use the `github` skill's issue-create pattern. Body must include the signature comment.

```bash
gh issue create --repo {repo} \
  --title "{category}: {one-line summary}" \
  --label "introspect,introspect:{category}" \
  --body-file - <<EOF
<!-- introspect-sig: {signature} -->

**Detected by:** introspect skill, $(date -u +%FT%TZ)
**Window:** last {window}
**Container:** {container}
**Session:** {session_id or "n/a"}

## What happened

{2-3 sentence description}

## Excerpt

\`\`\`
{up to 30 lines from the log around the finding}
\`\`\`

## Suggested next step

{one short hypothesis or area to investigate, if obvious — otherwise omit}
EOF
```

If the parent label `introspect` or the per-category label doesn't exist, create
both (idempotent — `--force` overwrites colors but is safe to re-run):

```bash
gh label create "introspect" --description "Filed by introspect skill" --color "5319E7" --force
gh label create "introspect:{category}" --description "Introspect finding: {category}" --color "BFD4F2" --force
```

### Step 6: Ledger

After processing every finding (filed, commented, or skipped), append one line
per finding to `context/memory/introspection.md`. Create the file with a header
if it doesn't exist.

```
{ISO8601 timestamp} | {category} | {action: new|dup|dup-stale|skipped|error} | {issue_num or "-"} | {short signature}
```

If the run produced zero findings, still append one line:

```
{ISO8601 timestamp} | clean | scan | - | window={window} source={LOG_FILE or container}
```

The clean-run line is intentional — it confirms the loop is alive, not silently broken.

## Volume management

A chatty hour can produce hundreds of MB of logs. Guard against context blowup:

- Always collect the log window into a temp file first (Step 1), whether the
  source is `$LOG_FILE` or `docker logs`; never paste raw logs into your context.
- Pattern-scan with `grep` to surface candidate lines before involving an LLM.
- For Step 3, read chunks of ~30k chars; summarize each chunk to a structured
  finding list before moving to the next.
- Cap excerpts in issue bodies at 30 lines.

## Failure modes

These cover the scheduled / file-issues path. When you're reporting to a user
instead, surface the same conditions as a plain message rather than a ledger
line — and `gh`/repo failures don't apply, since you're not filing anything.

- **`gh` not authenticated** — log to ledger as `error`, do not retry. User must set `GH_TOKEN`.
- **No log source** — `$LOG_FILE` missing/empty *and* `docker logs` unavailable: log to ledger as `error`. Suggest setting `LOG_FILE` (it's set by docker compose) or, for host-side runs, mounting `/var/run/docker.sock`.
- **Empty log window** — log a `clean` line and exit.
- **Repo not resolvable** — log to ledger as `error`, do not file anywhere.

Never crash the scheduler tick. Always exit with a ledger entry.

## Scheduling

This skill is built to run on a cron. Register it with the `schedule` tool
(entries persist in `context/schedules.db`). Add it, then keep it disabled
via `toggle` until you've confirmed `GH_TOKEN` and `INTROSPECT_REPO` are set
and `$LOG_FILE` is readable — docker compose points it at
`/app/workspace/curunir.log`; `toggle` again to enable:

```
schedule(action="add", id="introspect-hourly", cron="0 * * * *",
         skill="introspect",
         prompt="Scan the last hour of curunir's logs and file github issues for any new problems.")
schedule(action="toggle", id="introspect-hourly")   # disable until ready
```

When the scheduler fires this task, the skill body is loaded and prepended to
the prompt automatically (see `src/scheduler.py`), so the agent has full
instructions in-session.

## Security note

The in-container path reads `$LOG_FILE` and needs no Docker access at all —
prefer it. The `docker logs` fallback only matters for host-side runs; if you
ever do mount `/var/run/docker.sock` into the container to enable it, note that
that gives the agent root-equivalent control over the host Docker daemon. If
that's unacceptable, run the introspect cron on the host instead — point a
host-side curunir at the same `INTROSPECT_REPO` and let it shell out to
`docker logs` directly.
