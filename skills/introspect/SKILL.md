---
name: introspect
description: "Use when periodically reviewing curunir's own docker logs for regressions, errors, loops, tool-misuse, or context overflows and filing GitHub issues for novel findings. Trigger on a schedule (e.g. hourly via `context/schedules.json`), or when the user asks to scan logs / check on the agent's recent behavior. Dedups against open issues so repeated patterns become comments, not new tickets."
tools: bash
---

# Introspect

Self-hosted observability loop. Scan recent docker logs, classify findings, dedup
against open GitHub issues, and file new ones for novel problems.

> **Replaces `self-introspect`.** This skill subsumes the older `self-introspect`
> skill. If your `context/schedules.json` still references `self-introspect`,
> remove that entry and use `introspect-daily` (or the example schedule below)
> instead — they do the same job.

**Requires:**
- `gh` CLI authenticated via `GH_TOKEN` (see the `github` skill for details)
- Access to `docker logs`. The default Docker image ships the `docker` CLI and
  `docker-compose.yml` mounts `/var/run/docker.sock:ro` into the container, so
  this works out of the box on a fresh `docker compose up`. If you've removed
  the socket mount or are running outside compose, the host's `docker` CLI on
  `$PATH` works equivalently.

If neither is available, log a one-line failure to `context/memory/introspection.md`
and exit cleanly. Do not invent findings.

## Inputs

- **Window** — how far back to scan. Default `1h`. Override via the prompt.
- **Container** — what to scan. Resolution order:
  1. `$CURUNIR_CONTAINER` env var
  2. `hostname` self-lookup if running inside the container (returns the container ID, which `docker logs` accepts)
  3. `docker ps --filter "name=curunir" --format "{{.Names}}" | head -1`
- **Repo** — where to file findings. Resolution order:
  1. `$INTROSPECT_REPO` env var (`owner/name`)
  2. `gh repo view --json nameWithOwner -q .nameWithOwner` from the curunir clone
  3. Skip filing and surface a clear error in the ledger

## Workflow

### Step 1: Pull logs

```bash
docker logs --since={window} {container} 2>&1 > /tmp/introspect-logs.txt
wc -l /tmp/introspect-logs.txt
```

If the file is empty or `docker logs` errors, log the failure to the ledger and exit.

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
tracker and erode trust in the loop.

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
3. **If matched** — comment instead of creating:
   ```bash
   gh issue comment {num} --repo {repo} --body "Re-occurred at $(date -u +%FT%TZ). Session: {session_id}. Excerpt:\n\n\`\`\`\n{trimmed_log}\n\`\`\`"
   ```
4. **If novel** — file a new issue (Step 5).

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
{ISO8601 timestamp} | {category} | {action: new|dup|skipped|error} | {issue_num or "-"} | {short signature}
```

If the run produced zero findings, still append one line:

```
{ISO8601 timestamp} | clean | scan | - | window={window} container={container}
```

The clean-run line is intentional — it confirms the loop is alive, not silently broken.

**Dedup before appending `error` lines.** Operator misconfig (no docker socket,
no `gh` auth, repo unresolvable) repeats every tick and would otherwise spam
the ledger with identical errors. Before writing an `error` line, compare its
signature against the last line of the ledger; if it matches, skip the write.

```bash
sig="docker-unreachable:docker-cli-not-installed-and-no-docker-sock"
last=$(tail -n 1 context/memory/introspection.md 2>/dev/null || true)
case "$last" in
  *"| error |"*"| $sig"*) ;;  # same error already last — skip
  *) printf '%s | error | %s | - | %s\n' "$(date -u +%FT%TZ)" "$category" "$sig" \
       >> context/memory/introspection.md ;;
esac
```

Apply the dedup guard only to `error` lines. `new`/`dup`/`clean` entries should
always be appended so the ledger remains a faithful per-tick record of what the
skill saw and did.

## Volume management

A chatty hour can produce hundreds of MB of logs. Guard against context blowup:

- Always write `docker logs` output to a file first; never paste raw logs into
  your context.
- Pattern-scan with `grep` to surface candidate lines before involving an LLM.
- For Step 3, read chunks of ~30k chars; summarize each chunk to a structured
  finding list before moving to the next.
- Cap excerpts in issue bodies at 30 lines.

## Failure modes

- **`gh` not authenticated** — log to ledger as `error`, do not retry. User must set `GH_TOKEN`.
- **Docker socket unreachable** — log to ledger as `error`. Suggest the user mount `/var/run/docker.sock` or run the skill from the host.
- **Empty log window** — log a `clean` line and exit.
- **Repo not resolvable** — log to ledger as `error`, do not file anywhere.

Never crash the scheduler tick. Always exit with a ledger entry.

## Scheduling

This skill is built to run on a cron via `context/schedules.json`. Example
entry (disabled by default — flip `enabled` to `true` after confirming
`GH_TOKEN`, the docker socket, and `INTROSPECT_REPO` are configured):

```json
[
  {
    "id": "introspect-hourly",
    "cron": "0 * * * *",
    "skill": "introspect",
    "prompt": "Scan the last hour of docker logs and file github issues for any new problems.",
    "enabled": false
  }
]
```

When the scheduler fires this task, the skill body is loaded and prepended to
the prompt automatically (see `src/scheduler.py`), so the agent has full
instructions in-session.

## Security note

Mounting `/var/run/docker.sock` into the curunir container gives the agent
root-equivalent control over the host Docker daemon. If that's unacceptable
for your environment, run the introspect cron on the host instead — point a
host-side curunir at the same `INTROSPECT_REPO` and let it shell out to
`docker logs` directly.
