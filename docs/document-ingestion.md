# Document Ingestion Pipeline (design)

Status: steps 1–2 implemented (ingestion core + local-UI eager trigger);
steps 3–4 (read gate, dedup) pending. (2026-07-09)

## Problem

A large document entering a conversation today either bloats the context
(a no-arg `read` ships up to `MAX_TOOL_RESULT_CHARS` = 100k chars ≈ 25k
tokens into history, resent on every subsequent LLM call until trimmed) or
gets read in fragments (partial `offset`/`limit` views that risk
misunderstanding). Professional use — e.g. the finance persona — involves
conversations spanning **many** large documents at once, so neither mode
scales.

## Design

Between "staged on disk" (which already exists — `_stage_attachments`
writes uploads to `<uploads_dir>/<session_id>/<uuid>/<file>` and the agent
sees only a manifest) and "read into context," insert an **ingestion step**
that produces a **document card**. The conversation carries cards (~1k
tokens each); the raw text is consulted via targeted reads guided by the
card.

### Ingestion is a one-shot LLM call, not an agent

Full (preprocessed) document text goes into a single prompt; the model
returns the card; the ingestion context is thrown away. Modeled on
`memory_extractor.extract_learnings`, **not** on `delegate`:

- **Cheaper** than an agentic reader (document sent once, no loop
  resends) and **faster** (one round-trip — matters because the UI blocks
  on it).
- **Better cards**: the model sees the whole document at once — global
  structure, resolved cross-references — instead of chunked fragments.
- **Safe by construction**: zero tool surface. A malicious document can
  distort nothing but the wording of its own card; no execution, no
  writes, no egress. This is why *eager automatic* ingestion of untrusted
  uploads is acceptable.

Pipeline (deterministic Python around the one call):

1. Extract text (reuse `fs_tools`' `_BINARY_READERS` for PDF/DOCX/XLSX).
2. **Number the lines** in `exec_read`'s `N\tline` format (page markers
   for PDFs) so card references are addressable by later `read` calls.
3. `call_llm` with the card-template prompt + numbered text; record a
   `UsageRecord` under session id `ingest:<sha256>` so ingestion cost is
   visible in the usage dashboard.
4. Write `<staged-file>.card.md` next to the document.
5. Return card text for injection into the chat session's context.

**Oversize fallback**: docs beyond the one-shot window (~150k tokens)
go map-reduce — one-shot card per chunk, final merge call over the
chunk-cards. Still no tools; a size check and a loop, not a second
architecture.

### The card is a navigation map, not analysis

Owned by a `document-ingest` skill (persona-templatable — the finance
template always extracts statements, periods, key figures):

- Doc type, period, entities ("Q3 2025 10-Q, FY ending Sep").
- Key figures **with locations** (line/page refs).
- Section map with line ranges ("Income statement: lines 210–260").

Cards are deliberately **task-blind**: one generic card is amortized
across every question asked over the document's lifetime, including
questions not yet conceived. Answers come from targeted reads; the card
only routes.

### Two triggers, one function

- **Eager (UI upload path)**: upload → stage → ingest → card injected
  into context → **UI unblocks the message box** (the user cannot submit
  a question until the card is ready — this resolves the
  ingestion-vs-question race by construction). Ingestion failure surfaces
  in the UI and falls back to the raw path.
- **Lazy (read gate, catch-all)**: documents arriving by other doors
  (email attachments, agent-fetched filings). `exec_read` gains a stat
  pre-gate: no `limit` + file over threshold (its own env knob, separate
  from `MAX_TOOL_RESULT_CHARS`) → don't read-and-truncate; if a sibling
  card exists return it, else return a cheap structural preview (total
  lines/bytes + first ~50–100 lines) with a message routing the model to
  offset/limit, grep, or ingestion. Same card format, same skill, same
  one-shot function.

### Dedup

Hash document bytes (sha256); on re-upload of identical bytes, reuse the
existing card. Versioning/invalidation deferred.

### Grounding rule (finance guardrail)

Figures that matter — anything headed for `portfolio.db` or a
client-facing number — are verified against a targeted `read` of the
cited section, never quoted from the card. The card's per-figure location
refs make this a one-call check. This is what keeps lossy compression
from becoming misunderstanding.

## Kept as-is

- `_cap_tool_result` stays unchanged — it's a crash-prevention backstop
  (and still guards `bash`/`web_fetch`), not a policy knob.
- Small/medium files (< ~50k chars) keep whole-file reads: fragmenting
  them is a false economy.

## Deliberately out of scope

No embeddings, no vector store, no background indexer, no `delegate`
involvement. Also worth stealing later (independent of this pipeline):
Claude Code-style read dedup — per-session `(path, offset, limit, mtime)`
cache returning an "unchanged since last read" stub on identical
re-reads.

## Build order

1. Ingestion function + `document-ingest` skill (card template) — the core.
2. UI upload gating (local web UI / portal) + card injection into session context.
3. `exec_read` pre-gate (lazy trigger + structural preview).
4. Byte-hash dedup.
