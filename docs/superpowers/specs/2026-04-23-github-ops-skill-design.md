# GitHub Ops Skill — Design

**Date:** 2026-04-23
**Status:** Design approved, ready for implementation plan

## Problem

The agent has no general-purpose way to perform GitHub operations (file an issue, list issues, look up a PR, find a repo). The only GitHub-related skill is `git-contribute`, which is a full issue-to-merge lifecycle — too heavyweight for ad-hoc ops, and its description doesn't reliably trigger on requests like "create an issue in repo X".

Additionally, `gh` CLI is not installed in the container and no GitHub credentials are wired up, so `git-contribute` itself has never been runnable in Docker.

## Goals

- Enable ad-hoc GitHub operations (issues, read-only PR/repo lookups) via natural-language requests.
- Keep the existing `git-contribute` lifecycle skill unchanged in behavior; only tighten its description to avoid trigger collision.
- Install and authenticate `gh` CLI in the container using the project's established patterns for CLI tools and credentials.

## Non-Goals

- Creating PRs, pushing code, code review actions, releases, workflow/Actions management. These are either `git-contribute`'s job or deferred.
- A structured Python `github` tool under `src/tools/`. The `bash` tool plus `gh` CLI covers the need; a structured tool would duplicate `gh`'s surface without a clear win.
- Replacing `gh auth login`-style interactive flows. Auth is token-only.

## Architecture

Three independent changes:

1. **Container**: install `gh` in the Dockerfile; pass `GH_TOKEN` via the existing `env_file: .env` wiring.
2. **New `github` skill** (`skills/github/SKILL.md`): lightweight `gh`-CLI recipes for ad-hoc ops.
3. **Tighten `git-contribute` description**: narrow the trigger so it matches only the full lifecycle, not general GitHub ops.

No changes to `src/tools/`. The `bash` tool runs `gh` the same way `git-contribute` already does.

## Change 1 — Container & Auth

### Dockerfile

Add a new `RUN` block after the existing `apt-get` line to install `gh`. `gh` is not in Debian slim's default repos, so GitHub's apt source must be added first. Follow the standard snippet from the [official install docs](https://github.com/cli/cli/blob/trunk/docs/install_linux.md):

```dockerfile
# Install gh CLI (used by github and git-contribute skills)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*
```

`git` is already installed and does not need to change.

### `.env.example`

Append a new section, following the comment-header convention:

```
# GitHub CLI (used by github and git-contribute skills)
# Create a fine-grained PAT at https://github.com/settings/tokens
# GH_TOKEN=ghp_...
```

### docker-compose.yml

No change needed. `env_file: .env` already forwards all vars into the container; `gh` picks up `GH_TOKEN` automatically.

### Auth behavior

`gh` reads `GH_TOKEN` from the environment and uses it for all API calls. No `gh auth login` step. If `GH_TOKEN` is unset, `gh` returns a clear error message on first use. The skill's "Requires" note will document this.

## Change 2 — New `github` Skill

**Location:** `skills/github/SKILL.md` (single file, no sub-resources).

### Frontmatter

```yaml
---
name: github
description: "General-purpose GitHub operations via `gh` CLI: create/list/view/comment/close issues, list/view pull requests, search/view repositories. Use when the user wants to file an issue, look up issues or PRs, find a repo, or perform ad-hoc GitHub interactions. For full issue→implementation→merge lifecycle, use `git-contribute` instead."
---
```

### Scope

| Category | Ops | Notes |
|---|---|---|
| Issues | create, list/search, view (body + comments), comment, close | Full CRUD-lite |
| Pull requests | list, view (body, status, comments) | Read-only |
| Repositories | search, view basic info (description, default branch, topics) | Read-only |

**Explicitly out of scope:** creating PRs, pushing code, review submissions, releases, Actions/workflows.

### Body structure

1. **Requires** — `gh` CLI authenticated via `GH_TOKEN` env var.
2. **Repo targeting** — short paragraph: if the user specifies `owner/repo`, use it; otherwise derive from the current directory via `gh repo view --json nameWithOwner`; otherwise ask the user which repo they mean.
3. **Recipe sections** — one short section per op category, each showing the `gh` invocation with parameter placeholders. Style matches `git-contribute` (terse, copy-paste-friendly commands). Prefer `--json <fields> --jq '...'` for structured output the LLM can reliably parse.
4. **Tips** — always quote issue titles/bodies; use `--limit` on list ops to avoid pagination; prefer `gh search issues` over `gh issue list` when scoping across multiple repos.

**Target length:** ~100 lines. Lean on `gh`'s native syntax; do not reinvent wrappers.

## Change 3 — Tighten `git-contribute` Description

The current description correctly opens with "Autonomous bug fix and feature implementation lifecycle" but the trailing trigger sentence is broad enough that requests like "file a bug" could pattern-match.

**Rewrite the trigger sentence** in the frontmatter `description` to:

> "Trigger: user asks to pick up and implement an existing GitHub issue end-to-end (plan → PR → review → merge), or is invoked on a loop to work through an issue tracker. For filing issues, listing issues/PRs, or other ad-hoc GitHub ops, use the `github` skill instead."

No changes to the body of the skill — the workflow is unchanged.

## Testing & Verification

No Python code is added, so no pytest changes.

**Verification steps:**

1. `docker compose build` completes successfully with the new Dockerfile block.
2. Inside the container: `gh --version` prints a version; with `GH_TOKEN` set, `gh auth status` reports authenticated.
3. **Functional smoke test**: from the CLI, ask the agent "list open issues in jalemieux/curunir". Expected: agent loads `skills/github/SKILL.md`, runs `gh issue list ...`, returns results.
4. **Disambiguation smoke test**:
   - "file an issue titled X in repo Y" → loads `github`, not `git-contribute`.
   - "pick up issue 42 and implement it" → loads `git-contribute`, not `github`.

## Open Questions

None. All design decisions are settled:

- Auth via `GH_TOKEN` env var (confirmed).
- Split into two skills rather than widening `git-contribute` or building a Python tool (confirmed).
- `gh` install via apt in Dockerfile, matching the project's CLI-tool convention (confirmed).
