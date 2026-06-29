# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Local development
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Add API keys
python run.py                  # Start server (:8765)
python cli.py --host localhost # Connect CLI client

# Docker
docker compose up --build

# Tests
pytest tests/                          # All tests (async, ~200)
pytest tests/test_agent.py -v          # Single file
pytest tests/ -k "test_session"        # Pattern match
pytest tests/ --cov=src --cov-report=html  # Coverage (needs pytest-cov)
```

## Commit Conventions

- Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`
- Do not add Co-Authored-By trailers to commit messages.

## Architecture

Curunir is a configurable agentic LLM framework for building digital assistants. Python 3.12+, fully async (asyncio).

### Core Loop (`src/agent/agent.py`)

`Agent.handle()` is the heart: receive message → trim history (250k char limit) → build system prompt → call LLM (via LiteLLM) → execute the tool-call batch concurrently → loop (max 200 iterations) → return response. Tool calls in a single batch run in parallel via `asyncio.gather()`; results map back 1:1 to `tool_call_id` to satisfy the chat schema. History char estimation charges a fixed ~2000 chars per image block so image-heavy sessions age out of the window alongside text.

**Tool-executor backstop.** `_run_tool_call` wraps `execute_tool_call` in `try/except Exception`: any tool that raises an unanticipated exception is logged at `warning` and returned as a model-visible `role: tool` error (`"Error: tool '<name>' failed: ..."`, capped via `_cap_tool_result`) instead of propagating. Because `asyncio.gather` runs with `return_exceptions=False`, an uncaught raise here would otherwise escape `handle()` and kill the whole turn/session — so no single tool can crash the loop, and the model can route around the failure.

Context overflow is caught from LiteLLM exceptions; history is adaptively trimmed to half of `MAX_HISTORY_CHARS` (125k by default) and retried once. An empty LLM response (no text, no tool calls) is retried, then nudged with `"Continue."`, then fails.

**Per-tool-result cap (defense-in-depth).** `_cap_tool_result` truncates any single tool result longer than `max_tool_result_chars` (env `MAX_TOOL_RESULT_CHARS`, default 100k chars ≈ 25k tokens) *before* it enters history, appending a marker that nudges the model to re-read with `read` offset/limit or `grep`. This is independent of `_trim_history` (which only drops whole message groups and can't shrink one oversized message), so a single uncapped `read`/`bash`/`web_fetch` result can't blow past the context window and poison the session. Lower it for small-context models.

**Usage accounting.** After each successful `call_llm`, a `UsageRecord` (prompt/completion/cached/reasoning/image/audio tokens, `cost_usd`, `elapsed_sec`) is written to SQLite `context/usage.db` via `asyncio.to_thread` (see Usage Tracking below).

**Date/time signals (cache-safe split).** Time enters the prompt as two correctly-scoped signals, never one frozen string. (1) A **stable per-session "Conversation started at"** line is appended in `_get_session_prompt` and cached per session — sourced from `conversation_store`'s persisted `created_at` (set once, preserved across resume; a brand-new in-memory session falls back to a first-turn timestamp captured once). Because it's byte-stable within a session it lives *inside* the cached prefix. (2) A **live per-turn "Current date/time"** note is computed once at the top of `handle()` (`datetime.now().astimezone()`, tz-aware) and injected as the final, **non-persisted** message in every `messages = [system] + history` assembly (centralized via the `_assemble_messages` helper so it's never written into `history`). It sits at the suffix and is identical across the turn's tool-loop iterations, so the cacheable prefix (system + history) stays byte-stable while the model still gets a fresh "now" each turn — fixing resumed conversations that used to reason from the stale boot time. `static_prompt` itself carries no timestamp, making the static prefix truly static across sessions and the process lifetime.

**Cancellation.** `Agent.request_cancel(session_id)` sets a per-session `asyncio.Event` that the loop checks at the top of each iteration and before the tool batch starts. Channels call this out-of-band when the user requests a stop (the in_queue is blocked while `handle()` runs). Because the batch is dispatched with a single `asyncio.gather()`, a cancel that arrives **before** the batch starts stubs every call in it with an `(interrupted)` tool response (so each `tool_call_id` has a matching response); once the batch is in flight, all calls run to completion — there is no mid-batch skip. On the next iteration the outer cancel check fires, an `(interrupted)` assistant turn is appended, and `handle()` returns `"(interrupted)"`.

### Message Flow

```
Channel.start() → IncomingMessage → in_queue → agent_worker → Agent.handle()
                                                                    ↓
                                                          tool execution loop
                                                                    ↓
                                                OutgoingMessage → out_queue → route_outbound() → Channel.send()
```

### Entry Point (`run.py`)

Wires everything together in a TaskGroup with concurrent coroutines: channel listeners, agent worker, outbound router, memory extraction (hourly), and scheduler.

### Channels (`src/channels/`)

- **WebSocket** (`ws.py`): Primary CLI interface on port 8765. Session ID is fixed `"cli"`. Binds `127.0.0.1` by default; gated by an Origin allowlist (localhost + missing/`null`) and a pairing token written to `context/.ws-token` (mode 0600). The CLI auto-loads the token from that file or `CURUNIR_WS_TOKEN`; `WS_ALLOWED_ORIGINS` extends the allowlist. Docker compose overrides `WS_HOST=0.0.0.0` so the published port works inside the container — network isolation + the token are the access controls there.
- **Email** (`email.py`): [Fastmail](https://fastmail.com) over **IMAP (inbound) + SMTP (outbound)** via `FastmailClient` (`fastmail.py`) on custom domain `curunir.ai` — **not** Gmail/Google, and no longer deadsimple.email. Stdlib `imaplib`/`smtplib` wrapped in `asyncio.to_thread` (zero new deps). Session ID is thread ID (derived from the `References`/`In-Reply-To` root, else the message's own `Message-ID`). Polls the INBOX every 60s (`EMAIL_POLL_INTERVAL`) using a persisted `(created_at, message_id)` **discovery cursor** (`_email_state.py`) keyed on the RFC822 `Message-ID` header and `Date`/INTERNALDATE. Spam is filtered server-side into Fastmail's Junk folder (which we don't poll), so the channel's `spam_score` guard is a no-op-safe default. Sender allowlist on inbound; recipient allowlist on outbound. Threading is built client-side (`In-Reply-To`/`References`), and replies carry a **stable generated `Message-ID`** (derived from the inbound id) reused across retries so a duplicate delivery is dedupable — SMTP has no idempotency key. Configured by `FASTMAIL_USER` / `FASTMAIL_PASSWORD` / `FASTMAIL_INBOX` (see Key Environment Variables).

  **Discovery vs. delivery (durable pending-reply ledger).** The cursor only means "seen" — it advances at poll, long before a reply is actually sent. A separate **pending-reply ledger** (`pending: dict[message_id → PendingReply]` in `_email_state.py`) means "done": every enqueued inbound is recorded *before* it hits the agent queue and only removed once its reply is confirmed sent, so advancing the cursor can no longer drop anything. A reply that fails to send (`FastmailError`: DNS/network/SMTP error) is no longer silently dropped — its computed body is persisted (`status=retry`) and a poll-tick **drain loop** (`_drain_retries`) re-sends it with exponential backoff (no agent re-run), dead-lettering (`status=dead`) after `EMAIL_SEND_MAX_RETRIES` and escalating at ERROR. On startup the channel **re-drives** the ledger: `status=queued` (agent crashed pre-reply) re-enqueues the inbound; `status=retry` (send crashed) re-sends the stored reply. `EMAIL_SEND_RETRY_BACKOFF` seeds the backoff; `EMAIL_FAILURE_ALERT_THRESHOLD` consecutive send/poll failures triggers a one-shot ERROR escalation.

  **Boot-state classifier.** `EmailState.load` distinguishes a genuine first run (`FileNotFoundError` → blank, allowed to fast-forward the cursor to `now` and skip pre-existing mail) from a **corrupt** state file (`JSONDecodeError`/`ValueError` → `corrupt=True`). On corrupt, `start()` logs ERROR + escalates and does **not** fast-forward or poll, so a lost watermark can't silently skip existing mail — an operator must repair/remove the file. The on-disk format renamed `watermark_*` → `cursor_*` (still reads the old key for back-compat; mirrors both on write).
- **Portal** (`portal.py`): Outbound WebSocket to a hosted portal (`CURUNIR_PORTAL_URL` + `CURUNIR_PORTAL_TOKEN`). Container dials portal; portal multiplexes browser ↔ container. Session ID is `"portal"`. See `portal/` directory for the portal service.
- **Local Web UI** (`local_web.py`): An operator-only web console served *from* the container itself — the co-located counterpart to the remote Portal. Off by default; enabled by `LOCAL_UI_ENABLED`. Hosts a **FastAPI + uvicorn** app bound to `LOCAL_UI_HOST` (default `127.0.0.1`, port `LOCAL_UI_PORT` default `8766`). Session ID is fixed `"local"`. Two halves:

  **Read panels (the unique co-located value).** Read-only REST endpoints (`/api/usage`, `/api/portfolio`, `/api/crm`, `/api/schedules`, `/api/memory`, `/api/memory/file`) backed by thin adapters in `src/local_ui/readers.py` over the *existing* read APIs (`UsageStore.summary`, `portfolio.engine.*`, `crm.engine.*`, `scheduler._load_tasks`, sandboxed `context/memory/` walks). No new query logic — the UI can't drift from `python -m src.usage` / `portfolio.py` / `crm.py`. `memory_file` is path-traversal guarded (rejects `..`/absolute paths that escape `context/memory/`).

  The **Usage** tab is a token dashboard (no dollars shown): in/out/cached summary cards, a **daily stacked-bar trend** (input / output / cached per day, inline HTML bars — no chart CDN, so it works offline), and a **breakdown** toggled across *conversation-or-job* / model / day. The by-session view (`UsageStore.summary(group_by="session")`) collapses each scheduled job's per-run sessions (`sched:<id>:<ts>` → `sched:<id>`) and `readers.usage_summary` joins the conversation store to label rows with real titles + a channel badge; `cost_usd` is still returned by the API but never rendered. `engine`-style math stays in the store — the UI only sums for card totals.

  The **Balance Sheet** tab is a single-page dashboard (not the old flat tables): a net-worth hero with a *values-as-of* staleness caveat (the `value_asof` range, since stored values aren't necessarily live), an **allocation bar** rendering `rollup()`'s positive buckets as net-worth composition (debt shown as a separate Liabilities line), **holdings grouped by class** into collapsible sections with per-class columns and subtotals (retirement folds under Equities, mirroring the rollup), and a **trades + realized-P&L** section (`trade_history` + `realized_pnl`) scoped to the current fiscal/calendar year. `portfolio_overview()` is extended to also return `unrealized`/`trades`/`realized`/`as_of`; the math stays in `engine` (`engine.unrealized` added) so the UI never re-sums. Read-only — all writes stay in chat/CLI.

  The **CRM** tab is the marketing analog of the Balance Sheet tab, backed by `crm_overview()` → `/api/crm` (`available=False` with empty payloads until a lead exists). It renders pipeline-by-stage cards, leads grouped by stage into collapsible sections, and a recent-activity (interaction ledger) panel — modeled on `loadPortfolio`, reusing the same group/badge styling. The tab is present for any persona but only *useful* under marketing (which alone allowlists the `crm` skill that unlocks writes), mirroring how the Balance Sheet tab is always present but finance-centric. Read-only — all writes stay in chat/CLI/tool.

  **Schedule editing (the one write surface).** Four token-gated mutating routes — `POST /api/schedules` (create), `PUT /api/schedules/{id}` (edit cron/prompt/skill/enabled), `POST /api/schedules/{id}/toggle`, `DELETE /api/schedules/{id}` — delegate straight to `schedule_store.engine` (`create`/`update`/`toggle`/`delete`), passing `skill_allowlist=config.skill_allowlist` exactly as the `schedule` tool does. The routes only map `ValueError` → HTTP 400; all validation (cron, duplicate id, skill allowlist) stays in the engine, so this surface can't drift from the `schedule` tool or the scheduler. The SPA's Schedules tab is correspondingly editable (per-row Edit/Enable-Disable/Delete + a New-schedule form, with a client-side next-runs cron preview that is convenience-only — the server stays authoritative). `id` is immutable (no engine rename); changing one is delete + create. Editing is loopback-bound and token-gated — the same trust boundary as the chat bridge, which can already drive the agent — so it widens no exposure.

  **Chat (reuses the portal frontend + wire protocol).** Serves a self-contained SPA (`src/local_ui/static/index.html`) that speaks the *same* JSON protocol as the portal (`user_message`/`agent_message`/`history_snapshot`/`skills_snapshot`/`conversations_snapshot`), but `/ws/browser` is bridged **directly** into the local agent queues (`in_queue`/`route_outbound`) instead of relayed through a remote container. The portal's multiplex routing, Postgres, and sign-in are deliberately **not** reused — a co-located console is single-user, single-socket.

  **Conversation sidebar (multi-conversation parity with the portal).** Although there's one browser socket, it drives **many** conversations: `_handle_inbound_frame` resolves `session_id` per-frame (`payload.session_id` → `LOCAL_SESSION_ID`) for the history/skills/slash/chat-enqueue/interrupt paths, and snapshot frames echo the requested `sid` so the shared chat module's per-session filter accepts a resumed transcript. A `conversations_request` is answered with a `conversations_snapshot` from a `conversations_provider` (wired in `run.py` to `agent.conversations_snapshot()` — which already drops email + scratch — plus a `sched:*` filter so only interactive conversations list). The SPA's Chat tab gains a left sidebar (scoped to that tab; read panels stay full-width) listing conversations with title · channel badge · relative time; click resumes (`chat.rebind()`), **+ New** mints a fresh `crypto.randomUUID()` (the legacy `"local"` row stays resumable), and per-row delete routes through the existing **`clear`** command (extract-then-delete, parity with the portal) rather than a raw `conversation_store.delete`. The shared chat module (`chat.js`) needed zero changes — it already reads `getSessionId()` live and exposes `onSocketOpen`/`onUnhandledFrame`/`onTurnFinal` hooks + `rebind()`.

  **Security** mirrors `ws.py`: an Origin allowlist (loopback default, extended by `WS_ALLOWED_ORIGINS`) plus the shared `context/.ws-token` pairing token. REST routes require the token (`?token=` query or `X-Curunir-Token` header); `/ws/browser` requires both an allowed Origin and the token (query param). Open it at `http://<host>:<port>/?token=<token>` — the SPA reads the token from the URL. Docker compose overrides `LOCAL_UI_HOST=0.0.0.0` and publishes `8766` (network isolation + token are the controls there, same rationale as `WS_HOST`). The console is **read panels + chat + schedule editing**; all other writes stay with the existing tools/skills.
- **Router** (`router.py`): Routes outgoing messages back to the originating channel.

Channels implement a protocol: `async start()` to listen, `async send(msg)` to respond.

**Shared helpers.** `fastmail.py` (IMAP/SMTP client: envelope→dict normalization, thread-root derivation, MIME assembly, recipient allowlist, stable reply Message-IDs), `_attachments.py` (decode/stage/enrich pipeline shared by WS + Portal, with symlink / Windows-reserved-name / Unicode-normalization defenses and per-type size caps), and `_email_state.py` (atomic watermark persistence) back the channels above.

**Interrupts.** WS, Portal, and Local Web UI channels accept an optional `cancel_session=agent.request_cancel` callback. When the client sends `{"command": "interrupt"}`, the channel routes it directly to the callback instead of enqueuing it (the agent_worker is blocked inside `handle()` and wouldn't drain the queue in time). The CLI (`cli.py`) wires Ctrl-C to send this frame while the agent is busy, via `loop.add_signal_handler(SIGINT, ...)`. While the prompt is active, prompt_toolkit reads Ctrl-C as a key in raw mode so the signal handler doesn't fire there — Ctrl-C at the prompt still exits.

### Tools (`src/tools/`)

**Default tools:** glob, grep, read, edit, write, bash, load_skill, web_fetch, delegate, schedule, attach

**Opt-in tools** (unlocked when a skill's frontmatter `tools:` requests them): `to_audio`, `portfolio`, `crm`

- Schemas registered in `schemas.py` via `_register()`
- Dispatch in `dispatcher.py` routes by name to executor functions
- Sync executors wrapped in `asyncio.to_thread()`; async executors (e.g. `delegate`) awaited directly
- `delegate` spawns a sub-agent restricted to `_SUB_AGENT_TOOLS` (no `delegate`, so sub-agents cannot recurse)
- Opt-in unlock is session-scoped: when `load_skill` runs, the skill's `tools:` are added to that session's tool set and schemas refresh for the next iteration

See [`src/tools/README.md`](src/tools/README.md) for detailed documentation on the tool registry, dispatch pipeline, executor implementations, and how to add new tools.

### Skills (`src/skills.py`, `skills/`)

Each skill is a directory with a `SKILL.md` file using YAML frontmatter:
```yaml
---
name: my-skill
description: When to use this skill
tools: attach            # Optional: comma-separated opt-in tools
hidden: true             # Optional: keep in registry but omit from the system-prompt catalog
portal_summary: "..."    # Optional: user-facing line; lists the skill in the portal Skills panel
portal_starter: true     # Optional: also surface as an empty-page starter (requires portal_summary)
---
```

`hidden: true` skills stay loadable (`load_skill`, `/skill-name`) but are
excluded from the manifest, so the agent won't route to them on its own —
use it to trial a new skill before GA without bloating the catalog.

The portal has two independent visibility gates. `portal_summary` is the
browse-panel gate: a skill appears in the portal's Skills panel only if it
sets `portal_summary` (the user-facing one-liner shown there). `portal_starter`
is the empty-page gate: it additionally surfaces the skill as a
"What would you like to do?" starter row. Starters are a subset of the
browse panel — `portal_starter` without `portal_summary` is ignored (the
skill is excluded everywhere and a warning is logged). `hidden` skills never
appear in the portal regardless of these flags.

Manifest auto-built at startup from all `SKILL.md` files and included in the system prompt. Agent loads full skill content on demand via `load_skill` tool.

### Slash Commands (`src/slash_commands.py`)

Two-layer dispatcher, invoked by `ws.py` and `portal.py` before a message reaches the agent. (1) An **intercepted** registry of LLM-free handlers: `/help`, `/skills`, `/clear` (aliases `/new`, `/reset`). (2) A **skill-forcing fallback**: `/<skill-name>` is rewritten into a synthetic `"Use the <skill> skill. {args}"` prompt. Hidden skills route via an explicit `load_skill` instruction with "do not substitute another skill" language to stop the model from pattern-matching to a similarly-named visible skill. The persona allowlist is enforced here too — `/<skill>` outside the active persona's allowlist is rejected.

### Personas (`personas/`, `src/persona.py`)

A persona is a deployment bundle selected at boot via `CURUNIR_PERSONA=<name>`.
There is no "no persona" code path — unset falls back to `personas/default/`,
which ships the full skill catalog and the baseline behavior prompt. Three
bundles ship today: **`default`** (full catalog, no allowlist), **`finance`**
(balance-sheet / position-tracking + research; allowlists ~27 skills, declares
`FRED_API_KEY` / `BRAVE_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY`), and
**`marketing`** (GTM pipeline + competitive intel; allowlists ~25 skills).

`personas/<name>/persona.yaml` declares an optional **absolute** skill
allowlist (omit `skills:` to allow every skill on disk) and key *names* for
a soft startup warning. Core tools are universal — personas do not curate
them. The allowlist is plumbed through `build_skill_manifest`, `load_skill`,
`portal_skill_list`, and the slash-command resolver so a curated persona
cannot reach skills outside its allowlist.

`personas/<name>/prompts/*.md` is read directly from the bundle (sorted by
filename, e.g. `10-domain.md` then `20-guardrails.md`) and appended to the
system prompt after `context/identity.md`. **This is where framework behavior
now lives** — the legacy `context/behavior.md` is no longer read (see Context
Directory). These files are framework/specialty content and are **not**
bootstrapped into `context/` — only user-edited content lives there. API-key
*values* are an operator concern (env/.env), never declared in skills or code.

Every persona bundle ships the same **no-general-knowledge guardrail**: the
agent must ground external factual claims in a tool/skill result (memory →
skills → `web_fetch`) rather than answering from training, unless the user
explicitly asks for its own opinion or recall. It lives in each bundle's
prompts (`default/prompts/behavior.md`, `finance`/`marketing`
`prompts/20-guardrails.md`) — there is no shared cross-persona prompt layer, so
the canonical wording is repeated per bundle by design.

### Portal Service (`portal/`)

Standalone FastAPI app deployed to Render, separate Python project from the curunir container. See [`portal/README.md`](portal/README.md). Contains its own pyproject.toml, Dockerfile, render.yaml, and tests/. The curunir container talks to it via PortalChannel.

### Portfolio / Balance-Sheet Engine (`src/portfolio/`)

Deterministic SQLite-backed personal balance sheet, powering the finance persona's "position tracking is tool-backed, never prose" rule. `db.py` defines a wide `assets` table + `liabilities` table, an append-only `trades` ledger, and three canned views — `v_networth`, `v_rollup_by_class`, `v_collectibles_pnl` — so reads never re-sum. `engine.py` holds all logic: validated writes (`add_asset`/`update_asset`/`remove_asset`/`add_liability`/`import_rows`), reads (`networth`/`rollup`/`list`/`show`/`re_equity`/`pnl`/`unrealized`/`query`/`trade_history`/`realized_pnl`), deterministic `refresh()` re-pricing market classes via yfinance, and markdown rendering. Real-estate equity nets linked mortgages so the rollup sums to net worth without double-counting. The store lives at `context/memory/portfolio.db`. Three surfaces reach the engine: the opt-in `portfolio` tool (`src/tools/portfolio_tool.py`, `{action, args}` → JSON; unlocked by the `balance-sheet` skill), a CLI (`skills/balance-sheet/portfolio.py`), and the `balance-sheet` skill itself.

**Trade ledger (active, specific-lot).** `record_buy`/`record_sell` are the active entry point for position changes on qty-bearing classes (equity, physical): a buy mints a **new lot** (`cost_basis = qty·price + fees`, `acquired = trade_date`) and logs a `buy` trade; a sell draws down a **named lot** (`asset_id` — specific-lot, no cross-lot auto-split), computes realized P/L (`(qty·price − fees) − qty·per-share-basis`), flags long/short-term, deletes the lot at zero qty, and logs a `sell` trade. The `trades` row's `asset_id` is a soft reference (no FK) so a closed lot's deletion leaves the trade as durable history. `trade_history()` (filter by ticker/account/side/since) and `realized_pnl()` (split short/long-term, filter by year) read the ledger. Surfaced as `buy`/`sell`/`trades`/`realized` on both the tool and CLI. Out of scope: wash-sale detection, cross-lot FIFO, average-cost, non-qty assets.

**Snapshot history (append-only time-series).** The store otherwise keeps *current state only* (`refresh()` overwrites `value`/`value_asof` in place). The snapshot subsystem freezes point-in-time state so any date can be recalled and diffed. Three tables (`db.py`): `snapshots` (one row per capture — totals + metadata) plus `snapshot_assets` / `snapshot_liabilities` holding **frozen copies** of each child row (soft `snapshot_id` ref, no FK to the live tables) so a snapshot survives a later sale/deletion/re-pricing; a `v_snapshot_networth` view gives net-worth-over-time without re-summing. Engine functions: `snapshot()` (dedup-aware on calendar-date + trigger, `force=True` overrides), `list_snapshots()` (newest-first, `since`/`until`), `show_snapshot()` (by id, date, or `latest`), `diff_snapshots()` (net-worth/asset/liability deltas + per-holding gained/lost/new/closed, matching by `asset_id` then `(class,label,ticker)`). `refresh(snapshot_before=True)` freezes the pre-refresh state first (default off → existing behavior unchanged). Because the snapshot tables are plain, the read-only `query` action covers all ad-hoc time-series SQL for free. Surfaced as `snapshot`/`snapshots`/`show_snapshot`/`diff_snapshots` on the tool and CLI (`snapshot`/`snapshots`/`show-snapshot`/`diff-snapshot`). Out of scope: retention/pruning, backfilled historical market prices.

### CRM Engine (`src/crm/`)

Deterministic SQLite-backed mini-CRM, the marketing persona's direct analog of the portfolio engine — powering a "pipeline tracking is tool-backed, never prose" rule for leads. Mirrors the portfolio's four-tier shape, parameterized to a `crm` domain so the engine is harness-agnostic (pure SQLite + stdlib, zero agent imports, independently testable). `db.py` defines a wide `leads` table (with a JSON `extra` overflow column) + an append-only `interactions` ledger, and two canned views — `v_pipeline_by_stage`, `v_lead_latest_activity` — so reads never re-aggregate. `engine.py` holds all logic: validated writes (`add_lead`/`update_lead`/`set_stage`/`remove_lead`/`log_interaction`/`import_rows`), reads (`list_leads`/`show`/`pipeline`/`activity`/`query`), and markdown rendering. Stages are `new → contacted → qualified → trial → won/lost` (hardcoded `STAGES`); `add_lead` hard-rejects an exact-duplicate email (`UNIQUE(email)`, nullable) and warns on a near-duplicate name; `set_stage` validates the stage and logs a `stage_change` interaction so the pipeline is auditable. The `interactions.lead_id` is a soft reference (no FK) so a lead's deletion leaves its history as a durable ledger. The store lives at `context/memory/crm.db`. Three surfaces reach the engine: the opt-in `crm` tool (`src/tools/crm_tool.py`, `{action, args}` → JSON; unlocked by the `crm` skill, marketing-persona allowlist), a CLI (`skills/crm/crm.py`), and the `crm` skill itself. The driving ingestion use case is a single `add_lead(source="beta-signup", ...)` call so a later webhook/scheduled job can drive it without re-plumbing (automated ingestion is out of scope for v1). Out of scope: per-deployment stage config, lead dedup/merge, automated ingestion.

### Memory (`src/memory_extractor.py`, `src/memory_indexer.py`)

Post-session, `extract_learnings()` calls the LLM with conversation history to extract facts → appends to markdown files in `context/memory/` → stores conversation summary in `context/memory/archives/conversations/`. After the archive write, `update_indexes()` (in `src/memory_indexer.py`) maintains two progressive-discovery indexes: `summaries/timeline.md` (chronological, newest-first) and `summaries/topics/<slug>.md` (one per touched entity — `projects`, `people-anna`, etc.). Indexes upsert by archive path so re-extraction of an in-flight session updates entries in place. `README.md` is the routing entry point (read on-demand by the agent and programmatically by the extractor); the index files under `summaries/` are the next layer down. Topical files (`profile.md`, `preferences.md`, etc.) and `README.md` are the only files in this directory that should be hand-edited.

### Context Directory (`context/`)

Local directory containing `identity.md` (agent persona, required), `memory/` (persistent facts), and the runtime SQLite stores `schedules.db` (cron tasks; see Scheduling), `portfolio.db`, `crm.db` (leads + pipeline; see CRM Engine), and `usage.db`. The system prompt reads `identity.md` (then layers `personas/<active>/prompts/*.md` on top — see Personas). **`behavior.md` is no longer read at boot** — framework behavior moved into the persona bundle's `prompts/`; a stray `context/behavior.md` has no effect. The `/identity` skill only edits `identity.md`. Use `sync-context.sh` to rsync from a remote machine before starting.

### Onboarding (`onboarding/`)

First-run scaffolding. New users fill `onboarding/questions.md`, then ask an LLM (curunir itself, Claude Code, etc.) to generate `context.default/identity.md` from those answers — `onboarding/README.md` has the prompt. `bootstrap.py` copies that file into `context/` on first launch (never overwriting existing files).

See [`onboarding/README.md`](onboarding/README.md) for the user-facing flow and the LLM generation prompt.

### Evals (`eval/`)

`python eval/run_evals.py` runs capture-only suites defined in `simple_evals.md` and `advanced_evals.md` (streamed to `eval/eval_results/`, no grading; supports `--max-loops` and resume).

The **graded** harness is persona-agnostic and lives in `eval/harness/`: `graders.py` (pure-function + LLM-judge graders, the `GRADERS` registry, `Result`/`grade_detailed`) and `runner.py` (a `SuiteConfig` + the generic WS-drive / grade / interactive-HTML-report engine; `python eval/harness/test_runner_sync.py` is the zero-cost frame-sync regression). A persona suite is a thin shim that builds a `SuiteConfig(name, title, tasks, results_dir, fixture_memory_dir)` and calls `runner.main`. Two ship: **`eval/finance/`** (`run_finance_evals.py` + `finance_tasks.py`, ~34 tasks) and **`eval/default/`** (`run_default_evals.py` + `default_tasks.py`, currently an empty `TASKS` placeholder to be populated from the capture-only prompts). The runner drives a running instance over `ws://localhost:8765`, grades with the shared graders + an LLM judge (separate from the system-under-test), and emits a self-contained interactive HTML report. The finance R/F/C/P/T/W task taxonomy (regression / failure-mode / composition / position-tracking / reconciliation / multi-turn-write) is anchored against the same portfolio CLI the agent uses, so grader and agent can't drift; position-tracking tasks seed `fixtures/portfolio.sql` into `context/memory/` and restore on exit. See `eval/finance/README.md`.

### Scheduling (`src/scheduler.py`, `src/schedule_store/`)

Cron tasks live in the SQLite store `context/schedules.db` (one `schedules` table: `id`, `cron`, optional `skill`, `prompt`, `enabled`, plus run-metadata columns `last_run` / `last_attempt_at` / `last_status` / `last_error`). `src/schedule_store/` is a reusable node modeled on the portfolio engine: `db.py` owns the WAL schema/connection; `engine.py` holds all logic — `create`/`update`/`delete`/`toggle`, `validate_cron`, unique-`id` (PK) + **skill-allowlist** validation, and scoped run-metadata writers `mark_attempt` / `mark_run`. Because every write is a `UPDATE ... WHERE id=?`, the scheduler's metadata bookkeeping and a user edit no longer clobber each other (this is what the JSON full-file rewrite couldn't do). Both the `schedule` tool and the scheduler go through `engine`.

The scheduler evaluates the table every ~60s via croniter (re-querying each tick, so edits take effect without restart). When due, it stamps `last_attempt_at` via `mark_attempt` *before* dispatch (so a slow/crashed task doesn't re-fire), optionally prepends the named skill's `SKILL.md`, and runs `handle()` in system-task mode under a per-run `sched:<id>:<ts>` session id; `mark_run` advances `last_run`/`last_status` only on success.

**Source of truth.** On boot, `run.py` only initializes the store (`schedule_db.init_db`); the SQLite table is the sole schedule source. The legacy `context/schedules.json` is no longer read or migrated — a stray one is inert. (The historical one-time JSON→SQLite import was removed; deployments already on SQLite are unaffected.)

### Usage Tracking (`src/usage_store.py`, `src/usage.py`)

`UsageStore` (WAL-mode SQLite at `context/usage.db`, `config.usage_db`) gets one `UsageRecord` per `call_llm` — written from the agent in a background thread. `python -m src.usage` reports it: `--window` (default `7d`), `--by model|day|session`, `--db PATH` (`session` collapses a scheduled job's per-run ids `sched:<id>:<ts>` → `sched:<id>`). Tracks all token classes (incl. cached/reasoning), `cost_usd` (nullable; set when the provider returns one), and latency.

## Testing Patterns

All tests are async (pytest-asyncio). Key fixtures in `tests/conftest.py`: `tmp_context`, `tmp_skills`, `agent_config`.

Mock LLM: `patch("src.agent.agent.call_llm", new_callable=AsyncMock)`

Key test files map 1:1 to modules: `test_agent.py`, `test_channels.py`, `test_tools.py`, `test_memory_extractor.py`, `test_scheduler.py`, etc.

## Key Environment Variables

See `.env.example` for full list. Critical ones:
- `MODEL` — LiteLLM format (e.g., `anthropic/claude-sonnet-4-20250514`)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY`
- `VISION_MODEL` — fallback vision model when `MODEL` is text-only. At boot, `litellm.supports_vision(MODEL)` is checked; if false, image attachments are described by `VISION_MODEL` and the description is sent to `MODEL` as text. If unset, images become a `[file (image, NKB) — no vision model configured]` text marker.
- `EMAIL_ENABLED`, `FASTMAIL_USER`, `FASTMAIL_PASSWORD`, `FASTMAIL_INBOX` (the From address; defaults to `FASTMAIL_USER`), `FASTMAIL_IMAP_HOST` (default `imap.fastmail.com`), `FASTMAIL_SMTP_HOST` (default `smtp.fastmail.com`), `EMAIL_ALLOWED_SENDERS`, `EMAIL_RESTRICT_OUTBOUND`, `EMAIL_POLL_INTERVAL`, `EMAIL_STATE_FILE`, `EMAIL_SEND_MAX_RETRIES` (default 5), `EMAIL_SEND_RETRY_BACKOFF` (default 30s), `EMAIL_FAILURE_ALERT_THRESHOLD` (default 5) — for the Fastmail IMAP/SMTP channel (no Google/Gmail or deadsimple vars anymore)
- `MAX_HISTORY_CHARS` — conversation history limit in chars (default 250000; lower for small-context models)
- `MAX_TOOL_RESULT_CHARS` — per-tool-result truncation cap in chars (default 100000 ≈ 25k tokens; defense-in-depth, lower for small-context models)
- `LOCAL_UI_ENABLED`, `LOCAL_UI_HOST` (default `127.0.0.1`), `LOCAL_UI_PORT` (default `8766`) — the loopback-bound local web console (`local_web.py`). Reuses the `context/.ws-token` pairing token + `WS_ALLOWED_ORIGINS` allowlist; no dedicated auth env vars
- `LOG_LEVEL` — set to `DEBUG` for detailed agent tracing
- `LOG_FILE` — path to a log file written via `RotatingFileHandler` (10MB × 3 backups). Docker compose sets this to `/app/workspace/curunir.log` so the introspection skill and `docker exec ... tail` can read agent activity. Unset → stderr only.
