---
name: github
description: "General-purpose GitHub operations via `gh` CLI: create/list/view/comment/close issues, list/view pull requests, search/view repositories. Use when the user wants to file an issue, look up issues or PRs, find a repo, or perform ad-hoc GitHub interactions. For full issue→implementation→merge lifecycle, use `git-contribute` instead."
---

# GitHub

Ad-hoc GitHub operations via the `gh` CLI.

**Requires:** `gh` CLI authenticated via `GH_TOKEN` env var.

## Repo targeting

Most commands accept a `--repo owner/name` flag. Resolution order:

1. If the user specifies `owner/repo`, pass it explicitly.
2. If the current directory is inside a clone, `gh` auto-detects it.
3. Otherwise, ask the user which repo.

To derive from the current dir: `gh repo view --json nameWithOwner -q .nameWithOwner`

## Issues

### Create

```bash
gh issue create --repo {owner/repo} --title "{title}" --body "{body}"
```

Optional: `--label "bug,good-first-issue"`, `--assignee @me`.

For multiline bodies, use `--body-file -` with a heredoc (see Tips).

### List / search

Single repo:
```bash
gh issue list --repo {owner/repo} --state open --limit 30 \
  --json number,title,labels,author,updatedAt
```

Cross-repo search (by author, assignee, label, or keyword):
```bash
gh search issues "{query}" --limit 30 \
  --json number,title,repository,url,state
```

### View (body + comments)

```bash
gh issue view {num} --repo {owner/repo} \
  --json number,title,body,state,labels,author,comments
```

### Comment

```bash
gh issue comment {num} --repo {owner/repo} --body "{comment}"
```

### Close

```bash
gh issue close {num} --repo {owner/repo} --comment "{reason}"
```

## Pull requests (read-only)

### List

```bash
gh pr list --repo {owner/repo} --state open --limit 30 \
  --json number,title,author,isDraft,labels,updatedAt
```

### View

Body, status, comments, and CI rollup:
```bash
gh pr view {num} --repo {owner/repo} \
  --json number,title,body,state,isDraft,author,reviews,comments,statusCheckRollup
```

> For **creating** PRs, pushing code, or running the review lifecycle, use the `git-contribute` skill instead.

## Repositories

### Search

```bash
gh search repos "{query}" --limit 20 \
  --json name,owner,description,stargazerCount,updatedAt,url
```

### View

```bash
gh repo view {owner/repo} \
  --json name,description,defaultBranchRef,topics,stargazerCount,url
```

## Tips

- Always quote issue titles and bodies — they often contain characters the shell will eat.
- Use `--limit N` on every list/search command to avoid pagination and unbounded output.
- Prefer `--json <fields> --jq '...'` for structured output. Parse and summarize; don't dump raw JSON back to the user.
- For long or multiline bodies, use `--body-file -` with a heredoc:
  ```bash
  gh issue create --repo {owner/repo} --title "{title}" --body-file - <<'EOF'
  {body line 1}
  {body line 2}
  EOF
  ```
- When the user hasn't specified a repo, ask — don't guess. Filing an issue in the wrong repo is disruptive.
- If `gh` returns an auth error, the container is missing `GH_TOKEN`. Tell the user to set it in `.env`.
