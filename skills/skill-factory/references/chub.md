# Context Hub (`chub`)

Community-maintained CLI that provides curated, LLM-optimized API documentation. 1000+ API docs, versioned, maintained by library authors and the community.

**Install:** `npm install -g @aisuite/chub`

## Workflow: search → get → annotate

### 1. Search for docs

```bash
chub search "stripe"
```

Returns matching doc IDs, available languages, and descriptions. Use `--tags`, `--lang`, or `--limit` to filter. No query lists everything.

### 2. Fetch docs by ID

```bash
chub get stripe/api --lang js
```

Returns clean markdown with YAML frontmatter — ready to use directly. Use `--full` to fetch all files (not just the entry point), or `--file <path>` for a specific file.

Multiple IDs in one call: `chub get stripe/api openai/chat --lang py`

### 3. Annotate for future sessions

After using a doc, attach notes about gotchas or project-specific context:

```bash
chub annotate stripe/api "Webhooks require signature verification with STRIPE_WEBHOOK_SECRET env var"
```

Annotations persist locally and appear automatically on future `chub get` calls. List all: `chub annotate --list`. Clear: `chub annotate stripe/api --clear`.

## Gotchas

- **`--lang` is required for `get`** when a doc has multiple language variants. Omitting it when variants exist will error. Check `search` output for available languages.
- **`--json` flag** on any command gives machine-readable output if you need to parse results programmatically.
- **Docs are versioned** — use `--version` on `get` to pin a specific version. Default is the recommended (latest) version.
