# Curunir — *the man of skill*

A configurable agent framework for building specialized digital assistants. Define an identity, add skills, connect channels — get a capable assistant tailored to your domain.

## Philosophy

Curunir is built on lessons learned from building multiple agentic loop-based assistants using various frontier models:

- **Models are best with bash.** Modern frontier models find the most agency when given shell access and file tools.
- **Skills are prompts.** Complex workflows are captured as markdown instructions the agent loads on demand. Markdown instructions that reference the base tools and any CLI tools available in the container.
- **Context rot is real.** Drift and noise degrade model performance. The system prompt stays minimal — identity, skill manifest, timestamp — and skills are loaded only when needed.
- **Memory is markdown.** Frontier models are very good at reading multi-layered structured markdown files. In our experimentation, this produced better results than sophisticated vector-based RAG pipelines.
- **Conversational onboarding** — first-run setup runs inside the agent (6 prompts: profile, preferences, personality). Slash commands `/onboarding`, `/profile`, `/preferences`, `/personality` are available afterwards to refresh any section.

## Architecture

```
   Channels                                     Core              LLM
   ───────────────────                      ──────────────    ────────────

   CLI / WebSocket    ┐     ┌──────────┐    ┌────────────┐    ┌──────────┐
   Email (IMAP/SMTP)  ┼────►│ in_queue │───►│            │◄──►│ LiteLLM  │
   Portal (hosted)    ┤     └──────────┘    │ Agent loop │    └──────────┘
   Local web console  ┘                     │  max 200   │
                                            │ iterations │    ┌────────────────┐
   CLI / WebSocket   ◄┐    ┌───────────┐    │            │◄──►│   tool batch   │
   Email (IMAP/SMTP) ◄┤    │ out_queue │    │            │    │ asyncio.gather │
   Portal (hosted)   ◄┼────┤ + router  │◄───│            │    └────────────────┘
   Local web console ◄┘    └───────────┘    └────────────┘

     Tools   glob  grep  read  edit  write  bash  load_skill  web_fetch
             delegate  schedule  attach
     opt-in  portfolio  crm  to_audio      ← unlocked by a skill's `tools:`
```

```
   Background workers (run.py TaskGroup)      Persistent state (context/)
   ─────────────────────────────────────      ───────────────────────────
   channel listeners                          conversations/       transcripts
   agent_worker                               memory/              markdown facts
   route_outbound                             workspace/           deliverables + scratch
   scheduler            cron → agent turn     schedules.db         cron tasks
   periodic_extraction  transcripts → memory  memory/portfolio.db  balance sheet
   periodic_dreaming    memory housekeeping   memory/crm.db        leads / pipeline
                                              usage.db             token/cost ledger
```

Messages arrive from any channel, enter the in-queue, and are processed by
the agent loop. The agent calls an LLM (via LiteLLM) with conversation
history and tool schemas, streams text deltas back to the channel, and
iterates up to 200 tool-calling rounds per turn. Each round dispatches its
whole tool batch concurrently through `asyncio.gather()`. Replies leave via
the out-queue and are routed back to the originating channel.

Three background loops run alongside the channels. The **scheduler**
evaluates cron tasks in the SQLite store `context/schedules.db` every ~60s
and submits due ones as system-initiated turns. **Extraction** is
disk-driven rather than session-driven: transcripts persist to
`context/conversations/`, and a periodic pass summarizes every conversation
that has settled (idle ~5 min) and grown since its last extraction, writing
durable facts into `context/memory/` and an archive summary under
`context/memory/archives/`. `/clear` forces the same extraction inline
before deleting the transcript. A nightly **dreaming** pass runs the
`dreaming` skill to keep the memory tree tidy. Per-call token usage and cost
land in a local SQLite ledger at `context/usage.db`.

Ctrl-C while the agent is working triggers a cooperative cancel: the
in-flight LLM call and current tool run to completion, any remaining tools
in the batch are stubbed with `(interrupted)`, and the turn returns cleanly.
Channels deliver the cancel out-of-band (the agent queue is blocked inside
`handle()`).

When the main model is text-only, image attachments are routed through
`VISION_MODEL` — a vision-capable sidecar that describes each image as text
— before reaching the main model. Boot fails fast if `MODEL` lacks vision
support and no `VISION_MODEL` is configured.

## Project Structure

```
curunir/
├── run.py                  # Entry point — wires channels, queues, agent, workers
├── cli.py                  # Standalone WebSocket CLI client
├── src/
│   ├── agent/              # Agent loop, system prompt builder, conversation store
│   ├── channels/           # CLI/WS, Email, Portal, Local Web UI channels and router
│   ├── local_ui/           # Loopback web console: read adapters + static SPA
│   ├── tools/              # Tool schemas, dispatch, and executors
│   ├── portfolio/          # SQLite balance-sheet engine (db.py + engine.py)
│   ├── crm/                # SQLite lead/pipeline engine (db.py + engine.py)
│   ├── schedule_store/     # SQLite schedule store (db.py + engine.py)
│   ├── config.py           # AgentConfig dataclass
│   ├── llm.py              # LLM interface (LiteLLM) with transient/mid-stream retry
│   ├── persona.py          # Persona bundle loader (allowlist + prompt layers)
│   ├── modules.py          # Module → gating skill → UI panel/endpoint mapping
│   ├── skills.py           # Skill manifest and loader
│   ├── slash_commands.py   # Slash dispatcher (intercepted + skill-forcing)
│   ├── document_ingest.py  # One-shot document-card ingestion
│   ├── memory_extractor.py # Conversation → durable memory facts
│   ├── memory_indexer.py   # Timeline + per-topic memory indexes
│   ├── scheduler.py        # Cron task runner (reads context/schedules.db)
│   ├── usage_store.py      # SQLite per-call token/cost ledger
│   └── usage.py            # `python -m src.usage` reporting CLI
├── skills/                 # Drop-in skills (each a dir with SKILL.md)
├── personas/               # Deployment bundles (default, finance, marketing, companion)
├── portal/                 # Standalone FastAPI portal app (separate project)
├── eval/                   # Capture-only suites + graded harness and persona suites
├── onboarding/             # First-run identity scaffolding
├── tests/                  # Async pytest suite
├── docs/                   # Deployment, local-LLM, document-ingestion, design notes
├── context/                # Gitignored runtime volume (mounted/configured)
│   ├── identity.md         # Assistant persona and instructions
│   ├── memory/             # Persistent markdown memory store (+ portfolio.db, crm.db)
│   ├── conversations/      # Per-session transcripts (source of truth)
│   ├── input/              # Drop-zone for user-supplied input files
│   ├── workspace/          # Everything the agent produces
│   │   ├── generated/      # Deliverables (research reports, memos, exported PDFs)
│   │   └── scratch/        # Transient/intermediate files (safe to delete)
│   ├── schedules.db        # SQLite cron-task store evaluated by scheduler
│   └── usage.db            # SQLite per-call token/cost ledger
├── workspace/              # Gitignored host volume for the rotating log (LOG_FILE)
└── Dockerfile              # Container with Python 3.12, ripgrep, pandoc, LaTeX, git
```

`context/` is the single gitignored runtime volume, and the split inside it
tracks file provenance: `identity.md`, `memory/`, `input/`, and the SQLite
stores hold everything supplied **to** the agent, while `context/workspace/`
holds everything it **produces** — deliverables under `generated/` (what the
local console's Files rail lists and what `attach` sends) and throwaway
working files under `scratch/`. Skills writing files to disk follow this
convention; see `src/tools/README.md` for the full output-path rules. The
separate top-level `workspace/` is only a bind-mount target for the rotating
log (`LOG_FILE`), not an agent output path.

## Quick Start

### Local

```bash
git clone https://github.com/jalemieux/curunir.git
cd curunir
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your API key
python run.py               # starts the WebSocket channel on :8765
python cli.py --host localhost   # in a second shell: connect the CLI client
```

On a fresh checkout `context/identity.md` doesn't exist yet, so the agent
auto-runs the `onboarding` skill on your first message — six prompts across
profile, preferences, and personality, the last of which writes
`context/identity.md`. Re-run any section later with `/profile`,
`/preferences`, `/personality`, or the whole thing with `/onboarding`. To
skip the conversation, pre-seed `context.default/identity.md` from
`onboarding/questions.md` instead — see
**[onboarding/README.md](onboarding/README.md)**.

### Docker (local dev)

```bash
# Agent only
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build curunir

# Full stack (agent + portal + postgres)
docker compose --profile portal \
  -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The portal and postgres services sit behind a `portal` compose profile, so
`--profile portal` is required whenever you want the hosted browser UI. The
dev override re-adds `build:` for both services so you iterate locally
instead of pulling from GHCR, bind-mounts `./portal` into the portal
container, and enables uvicorn `--reload`.

### Deployment

Production hosts pull a versioned image from GHCR
(`ghcr.io/jalemieux/curunir`) — they do not run a source checkout. The
portal deploys independently to render.com from its own GHCR image
(`ghcr.io/jalemieux/curunir-portal`). See **[docs/deployment.md](docs/deployment.md)**
for the host layout, the GHCR login flow, and `scripts/deploy.sh`.

### Local LLM backend

To run against a local model server (llama.cpp, Ollama, vLLM, LM Studio)
instead of a hosted API — including Apple Silicon GPU offload, context/swap
tuning, capping reasoning tokens, and the Docker `host.docker.internal`
wiring — see **[docs/local-llm.md](docs/local-llm.md)**.

### CLI controls

| Input | Effect |
|---|---|
| `/help` | Show available commands and skills |
| `/skills` | List registered skills |
| `/clear`, `/new`, `/reset` | Reset the session (and trigger memory extraction) |
| `/<skill-name> [args]` | Force the agent to use a specific skill (e.g. `/identity update my voice`) |
| `/verbose` | Toggle live tool-call output (CLI-local) |
| `/attach <path>` / `/detach <i>` | Stage or remove a file for the next message (CLI-local) |
| **Ctrl-C while the agent is working** | Send an interrupt — the agent finishes the in-flight tool, skips any remaining tools in the batch, and replies `(interrupted)` |
| Ctrl-C at the prompt | Exit the CLI |
| Ctrl-D at the prompt | Exit cleanly |

Slash commands have two layers: an explicit registry for utility ops
(`/help`, `/skills`, `/clear`), and a fallback that turns any
`/<skill-name>` into a skill-forcing prompt for the agent. They work
identically over the CLI WebSocket, the portal, and the local web console.
Channels forward slash text to `agent_worker` as `command="slash"` messages
— dispatch happens there so channels stay ignorant of the skill registry.
The persona allowlist is enforced at dispatch too, so `/<skill>` outside the
active persona's allowlist is rejected. Cancellation is the only
slash-adjacent action handled channel-side, as an out-of-band
`{"command": "interrupt"}` frame (bound to Ctrl-C in the CLI), because the
agent worker is blocked during a turn and can't drain the queue in time.

#### Email Channel (Fastmail — IMAP/SMTP)

The email channel uses [Fastmail](https://fastmail.com) over IMAP (inbound) and SMTP (outbound) on a custom domain (e.g. `curunir.ai`). Create the mailbox and an **app-specific password** in Fastmail settings, then set:

```bash
EMAIL_ENABLED=true
FASTMAIL_USER=jac@curunir.ai
FASTMAIL_PASSWORD=your_fastmail_app_password
FASTMAIL_INBOX=jac@curunir.ai          # the From address; defaults to FASTMAIL_USER
EMAIL_ALLOWED_SENDERS=alice@example.com,bob@example.com
```

The channel polls the INBOX every `EMAIL_POLL_INTERVAL` seconds (default 60) via IMAP, and sends replies via SMTP — text-only when there are no attachments, or a `multipart/mixed` message with explicit `In-Reply-To`/`References` threading headers when attachments are included. Spam is filtered server-side into Fastmail's Junk folder (which the channel never polls), so INBOX is pre-filtered. The discovery cursor (keyed on the RFC822 `Message-ID` and `Date`) is persisted to `./context/email_state.json` so restarts resume without reprocessing history. Replies carry a stable generated `Message-ID` reused across retries so a duplicate delivery (after a lost SMTP ack) is dedupable by the receiving server.

Delivery is decoupled from discovery so an outbound failure can't silently drop a message. Every inbound is recorded in a durable **pending-reply ledger** in the same state file the moment it is queued, and only cleared once its reply is confirmed sent. If a reply send fails (DNS/network/SMTP error), the computed reply is stored and re-sent with exponential backoff (`EMAIL_SEND_RETRY_BACKOFF`, default 30s) up to `EMAIL_SEND_MAX_RETRIES` (default 5) attempts before being dead-lettered and escalated at ERROR; `EMAIL_FAILURE_ALERT_THRESHOLD` (default 5) consecutive failures also escalates. On restart the ledger is re-driven (unanswered inbound re-enqueued, failed sends re-sent). A genuine first run skips pre-existing mail, but a *corrupt* state file is never fast-forwarded — the channel alerts and waits for an operator to repair or remove it.

See `.env.example` for the full list of email-related variables.

#### Portal Channel (hosted web UI)

The portal is a standalone FastAPI app (in `portal/`) that gives the agent a multi-user browser front end with email-link sign-in, per-tab sessions, and drag-drop attachments. The curunir container dials *out* to the portal over WebSocket on startup; the portal multiplexes each browser to the matching container.

Enable it by setting:

```bash
CURUNIR_PORTAL_URL=wss://your-portal.example.com/ws/agent
CURUNIR_PORTAL_TOKEN=<bearer-token-issued-by-portal>
```

Every finalized agent response in the portal carries a Copy / Print action row. Copy writes the response markdown to the clipboard; Print opens the browser's print dialog on a clean, paper-styled copy of that response, which can then be saved as a PDF.

See **[portal/README.md](portal/README.md)** for portal deployment and the local `docker compose --profile portal up` dev path.

#### Local Web UI (operator console served from the container)

A lightweight, operator-only web console served directly from the curunir
container — the co-located counterpart to the hosted portal. Where the
portal relays chat through a remote service, this UI reads the
container-local stores *directly*, through thin adapters
(`src/local_ui/readers.py`) over the same read APIs the CLIs use, so the
panels can't drift from `python -m src.usage` / `portfolio.py` / `crm.py`.

| Tab | What it shows |
|---|---|
| **Chat** | Full chat parity with the portal — same wire protocol, theme toggle, live tool ticker, attachments, skills picker — bridged straight into the local agent queues. A left sidebar lists conversations (title · channel badge · relative time) for resume/new/delete; a right rail lists `context/workspace/generated/` for one-click download. |
| **Usage** | Token dashboard over `context/usage.db`: in/out/cached cards, a daily stacked-bar trend (inline HTML bars — no chart CDN, works offline), and a breakdown toggled across conversation-or-job / model / day. Scheduled-job runs collapse `sched:<id>:<ts>` → `sched:<id>`. |
| **Schedules** | Collapsible card per cron task — scannable header (id · humanized cron · state · next fire), Markdown-rendered prompt on expand. The one **write** surface: create / edit / enable-disable / delete through token-gated REST routes that delegate to the same `schedule_store.engine` the `schedule` tool uses. |
| **Memory** | Sandboxed walk of the `context/memory/` tree, rendered as Markdown. |
| **Balance Sheet** | *(finance persona)* Net-worth hero with a values-as-of staleness caveat, an allocation bar, holdings grouped by class into collapsible sections with per-class subtotals, and year-to-date trades + realized P&L. |
| **CRM** | *(marketing persona)* Pipeline-by-stage cards, leads grouped by stage, and a recent-activity interaction ledger. |

Balance Sheet and CRM are persona-gated **modules** (`src/modules.py`): a
module renders only when the persona's allowlist names its gating skill
(`balance-sheet`, `crm`), and its endpoints 404 otherwise — after the token
check, so an unauthenticated probe still gets 401 and can't enumerate them.
Apart from schedule editing, the console is read-only; all other writes stay
with the existing tools and skills.

Off by default. Enable it with:

```bash
LOCAL_UI_ENABLED=true
# LOCAL_UI_HOST=127.0.0.1   # 0.0.0.0 inside Docker (compose sets this)
# LOCAL_UI_PORT=8766
```

It binds loopback and reuses the WS channel's `context/.ws-token` pairing
token and Origin allowlist — no separate auth. Open it at
`http://localhost:8766/?token=<token>` (the token is printed in the startup
log and stored in `context/.ws-token`).

## Attachments

Channels accept file uploads (portal drag-drop / file picker, email MIME parts, CLI paths) and stage them as a manifest on the inbound message. `run.py:build_multimodal_content` then converts that manifest into LiteLLM content blocks before the agent sees the message.

Supported formats:

| Format | Handling | Size cap (portal) |
|---|---|---|
| Images — PNG, JPEG, GIF, WEBP | Inlined as base64 `image_url` blocks (vision models) | 5 MB |
| PDF | Text extracted via `pypdf`, fenced text block tagged with filename + page count | 10 MB |
| DOCX | Text extracted via `python-docx`, fenced text block | 10 MB |
| Plain text — `.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.log`, `.xml`, `.toml`, `.ini`, etc. | Decoded as UTF-8, fenced text block | 256 KB |

Total upload size per message is capped at 20 MB. Unsupported formats are filtered out by the portal file picker (`accept=` allowlist) and rejected client-side; if a binary somehow reaches the backend, it falls back to a notice block describing the file rather than crashing.

**Large documents get a card, not a dump.** A one-shot, tool-free LLM call
(`src/document_ingest.py`) turns a document into a ~1k-token **document
card** — a navigation map with line references — written next to the staged
file as `<file>.card.md`. The conversation carries the card; the raw text is
consulted through targeted `read` calls the card points at. The local web
console ingests eagerly on upload for files over `DOC_CARD_MIN_BYTES`
(default 50 KB), and the `read` tool enforces the same discipline
everywhere: a no-`limit` read of a file over `READ_GATE_BYTES` returns the
card (or a numbered head preview) rather than the whole body. See
**[docs/document-ingestion.md](docs/document-ingestion.md)**.

To add a new inline format: branch in `build_multimodal_content` (`run.py`), add the parser to `requirements.txt`, extend the portal's `accept=` allowlist and `stageFile` validator (`portal/static/index.html`), and cover both with tests in `tests/test_build_content.py` (mock the parser to keep tests hermetic).

## Adding Skills

Drop a directory into `skills/` with a `SKILL.md` file:

```
skills/
└── my-skill/
    └── SKILL.md
```

The `SKILL.md` uses YAML frontmatter for discovery:

```yaml
---
name: my-skill
description: When to use this skill
---

# My Skill

Instructions the agent follows when it loads this skill...
```

Skills appear in the agent's system prompt as a manifest table. The agent calls `load_skill` to fetch full instructions on demand.

### Skill Visibility

Three optional frontmatter flags control where a skill shows up:

```yaml
---
name: my-skill
description: When to use this skill
hidden: true             # Omit from the system-prompt manifest
portal_summary: "..."    # List in the portal Skills panel (user-facing one-liner)
portal_starter: true     # Also surface as an empty-page starter (requires portal_summary)
---
```

- `hidden: true` keeps the skill in the registry — still loadable via `load_skill` and `/skill-name` — but drops it from the agent's manifest, so the agent won't route to it on its own. Use it to trial a skill before GA.
- `portal_summary` is the **browse-panel gate**: the skill appears in the portal's Skills panel only if this is set, and its value is the user-facing summary shown there.
- `portal_starter` is the **empty-page gate**: it additionally surfaces the skill as a "What would you like to do?" starter row. Starters are a subset of the browse panel — `portal_starter` without `portal_summary` is ignored (the skill is excluded everywhere and a warning is logged). `hidden` skills never appear in the portal.

### Skill-Requested Tools

Every session starts with the default tool set — `glob`, `grep`, `read`,
`edit`, `write`, `bash`, `load_skill`, `web_fetch`, `delegate`, `schedule`,
and `attach`. Skills can declare **additional** opt-in tools that only
become available once the skill is loaded. Add a `tools` field to the
frontmatter:

```yaml
---
name: balance-sheet
description: Track and report on the owner's balance sheet
tools: portfolio
---
```

When the agent loads this skill via `load_skill`, the listed tools are added to the agent's tool set for the remainder of the session. This keeps the default tool set lean while allowing skills to unlock capabilities they need.

**Available opt-in tools:**

| Tool | Unlocked by | Description |
|------|-------------|-------------|
| `portfolio` | `balance-sheet` | `{action, args}` interface to the SQLite balance-sheet engine — holdings, trades, net worth, snapshots. |
| `crm` | `crm` | `{action, args}` interface to the SQLite lead/pipeline engine — leads, stages, interactions. |
| `to_audio` | `conversation-to-audio` | Rewrite text into a spoken-word script and synthesize an MP3 via OpenAI TTS, registered as a response attachment. |

## Personas

A persona is a deployment bundle: an optional absolute skill allowlist and a
`prompts/` directory layered on top of `context/identity.md` in the system
prompt. Select one with `CURUNIR_PERSONA=<name>`. Unset falls back to
`personas/default/`, which ships the full skill catalog and the baseline
behavior prompt — so unset is itself a persona, not a special case. Core
tools are universal; personas curate skills, not tools.

Four bundles ship today:

| Persona | What it is |
|---------|------------|
| `default` | Full skill catalog, no allowlist, baseline behavior prompt. |
| `finance` | Local, private personal-finance assistant — capital allocation, position tracking, investment-thesis lifecycle, tax strategy. |
| `marketing` | Go-to-market assistant — product onboarding, ICP & positioning, GTM planning, competitive intelligence. |
| `companion` | Direct, accountability-focused life coach / confidant with memory-driven continuity. |

Each specialty bundle adds its own domain + guardrails prompts on top of
`context/identity.md`, declares the API key *names* it expects (values stay
in `.env`), and ships a `README.md` plus a `.env.<name>.example`. Every
bundle carries the same no-general-knowledge guardrail: external factual
claims must be grounded in a tool or skill result, not recalled from
training.

Some persona bundles also enable **modules** — vertical UI surfaces gated on
the allowlist (`src/modules.py`). The local console's Balance Sheet tab
appears only when the persona allowlists `balance-sheet`; the CRM tab only
when it allowlists `crm`. Gated REST endpoints 404 on personas that don't
own them.

```bash
cp personas/finance/.env.finance.example .env
CURUNIR_PERSONA=finance python run.py
```

## Evals

Two harnesses live in `eval/`, both driving a running instance over
`ws://localhost:8765`.

**Capture-only suites** send a list of prompts and record whatever comes
back — no grading:

```bash
# Core capabilities (tool use, planning, memory, instruction following)
python eval/run_evals.py

# Advanced (web search, deep research, delegation, cross-skill orchestration)
python eval/run_evals.py --file eval/advanced_evals.md

# Cap iterations per prompt
python eval/run_evals.py --max-loops 20

# Against a remote instance
python eval/run_evals.py --host myserver.example.com --port 8765
```

Results land in `eval/eval_results/` as timestamped JSON including the model
name, all prompts, responses, and tool calls.

- `eval/simple_evals.md` — core capabilities (no API keys needed)
- `eval/advanced_evals.md` — skills like web-search, deep-research, and delegation (requires `BRAVE_API_KEY` and network access)

**Graded suites** add pure-function graders plus an LLM judge (a separate
model from the system under test) and emit a self-contained interactive HTML
report. The persona-agnostic engine is `eval/harness/` (`graders.py` +
`runner.py`); a suite is a thin shim that builds a `SuiteConfig` and calls
`runner.main`:

```bash
python eval/finance/run_finance_evals.py     # ~34 graded finance tasks
python eval/harness/test_runner_sync.py      # zero-cost frame-sync regression
```

Position-tracking tasks seed `eval/finance/fixtures/portfolio.sql` into
`context/memory/` and restore on exit, and are anchored against the same
portfolio CLI the agent uses so grader and agent can't drift. See
`eval/finance/README.md`.

## Configuration

Configuration is handled via `src/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `model` | `anthropic/claude-sonnet-4-20250514` | LLM model (any LiteLLM-supported model) |
| `max_iterations` | `200` | Max tool-calling rounds per turn |
| `max_history_chars` | `250000` | Conversation history limit; lower for small-context models |
| `max_tool_result_chars` | `100000` | Per-tool-result truncation cap (≈25k tokens) so one oversized `read`/`bash` can't poison the session |
| `read_gate_bytes` | `50000` | No-`limit` reads above this return a document card or head preview instead of the full body (`0` disables) |
| `persona` | `default` | Persona bundle under `personas/` (`CURUNIR_PERSONA`) |
| `identity_file` | `./context/identity.md` | Path to persona file |
| `context_dir` | `./context` | Path to context directory (memory, conversations, workspace, stores) |
| `skill_dirs` | `[./skills, ./context/skills]` | Directories scanned for skills in priority order (first-seen wins on name collision) |
| `vision_model` | unset | Vision-capable sidecar used when `model` is text-only (`VISION_MODEL`) |

API keys are set via environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, etc.). See `.env.example` for the full list.

Useful operational env vars:

- `LOG_FILE` — path to a rotating log file (10MB × 3 backups). Docker compose sets this to `/app/workspace/curunir.log` so the introspection skill can read agent activity.
- `LOG_LEVEL=DEBUG` — verbose agent tracing.

Per-call token usage and cost are persisted to `context/usage.db` (SQLite). Inspect with:

```bash
python -m src.usage --window 7d
```

## License

TBD
