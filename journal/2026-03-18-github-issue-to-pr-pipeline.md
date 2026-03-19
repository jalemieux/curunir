# GitHub Issue-to-PR Pipeline — Design Notes

## Problem Statement

Once system-initiated triggers and cron jobs are in place, we need an end-to-end autonomous workflow that picks up GitHub issues and drives them through to merged PRs — with human review checkpoints.

## The Pipeline

```
cron: poll for new/assigned issues
  → read issue, understand requirements
  → come up with a plan
  → open a draft PR with the plan in the description
  → wait for PR comments / approval of the plan
  → implement the plan
  → push commits, mark PR ready for review
  → wait for review comments
  → address feedback, push fixes
  → repeat until merged or closed
```

## Key Design Points

### 1. Issue Triage

The cron job polls for issues (assigned to the agent, labeled, or matching some filter). For each new issue, the agent reads the issue body, linked context, and relevant code to understand what's being asked.

### 2. Planning Phase

Before writing any code, the agent produces a plan — posted as a draft PR description or PR comment. This is the first human checkpoint: the user reviews the plan and can redirect before any implementation work happens.

### 3. Wait-for-Comment Loop

This is the novel part. After posting a plan or pushing code, the agent enters a **wait state** — it doesn't keep polling in a tight loop, it sleeps until the next cron tick, checks for new comments, and reacts. The flow is:

- Post plan / push code → go idle
- Next cron tick → check for new comments on open PRs
- If comments exist → process feedback, push changes, go idle again
- If approved → mark done, move on

This turns the agent into an asynchronous collaborator rather than a synchronous tool.

### 4. Feedback Cycles

Each round of review comments triggers a new agentic loop: read the feedback, update the implementation, push, go idle. The agent needs to handle:

- Requests for changes to specific code
- Architectural pushback (may need to re-plan)
- Clarifying questions (post a comment back, wait again)
- Approval (clean up, squash if needed, done)

## Dependencies

- System-initiated agentic loops (no user message to kick things off)
- Cron/scheduler infrastructure
- GitHub API tool calls (issues, PRs, comments, reviews)
- A way to persist state across cron ticks (which issue is in which stage, which PRs are waiting for review)

## Open Questions

1. **State machine or implicit?** Do we model the issue→plan→implement→review stages explicitly, or let the agent figure out where it is each time from the PR/issue state?
2. **Concurrency**: Can the agent work multiple issues in parallel, or one at a time?
3. **Scope limits**: How do we prevent the agent from taking on issues that are too large or ambiguous? Should there be a complexity gate?
4. **Cost**: Each cron tick that processes comments burns tokens. Need to keep the per-tick cost low — probably a cheap model for the "anything new?" check, escalating to a stronger model for actual implementation.
