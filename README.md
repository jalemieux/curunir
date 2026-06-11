# Curunir — *the man of skill*

<img src="docs/curunir2.png" alt="Curunir" style="border-radius: 8px;" />

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
  Channels              Core                    LLM
  ────────          ──────────              ──────────
  CLI ──────┐
  Email ────┤
  Portal ───┤       ┌──────────┐        ┌──────────────┐
  Slack† ───┴──►  Queue  ──►  Agent Loop  ◄──►  LiteLLM  │
                    └──────────┘        └──────────────┘
                         │
                         ▼
                 ┌───────────────┐
                 │     Tools     │
                 │───────────────│
                 │ glob    read  │
                 │ grep    edit  │
                 │ write   bash  │
                 │ load_skill    │
                 │ web_fetch     │
                 │ delegate      │
                 │ schedule      │
                 │ attach*       │
                 └───────────────┘
                 * opt-in, loaded by skills
                         │
                         ▼
                 ┌───────────────┐
                 │   Memory      │
                 │  Extractor    │
                 └───────────────┘

  † planned
```

Messages arrive from any channel, enter a queue, and are processed by the agent loop. The agent calls an LLM (via LiteLLM) with conversation history and tool schemas, streaming text deltas back to the channel and iterating up to 75 tool-calling rounds per turn. Replies are routed back to the originating channel. A scheduler reads cron tasks from `context/schedules.json` and submits them as system-initiated turns. The memory extractor runs post-session (on `/clear` or `/new`, EOF, or a periodic timer) to extract durable facts into `context/memory/`. Per-call token usage and cost are persisted to a local SQLite ledger at `context/usage.db`.

Ctrl-C while the agent is working triggers a cooperative cancel: the in-flight LLM call and current tool run to completion, any remaining tools in the batch are stubbed with `(interrupted)`, and the turn returns cleanly. Channels deliver the cancel out-of-band (the agent queue is blocked inside `handle()`).

When the main model is text-only, image attachments are routed through `VISION_MODEL` — a vision-capable sidecar that describes each image as text — before reaching the main model. Boot fails fast if `MODEL` lacks vision support and no `VISION_MODEL` is configured.

## Project Structure

```
curunir/
├── run.py                  # Entry point — wires channels, queues, agent
├── cli.py                  # Standalone WebSocket CLI client
├── src/
│   ├── agent/              # Core agent loop and system prompt builder
│   ├── channels/           # CLI/WS, Email, Portal, Local Web UI channels and router
│   ├── local_ui/           # Loopback web console: read adapters + static SPA
│   ├── tools/              # Tool schemas, dispatch, and executors
│   ├── config.py           # AgentConfig dataclass
│   ├── llm.py              # LLM interface (LiteLLM)
│   ├── memory_extractor.py # Post-session memory extraction
│   ├── scheduler.py        # Cron task runner (context/schedules.json)
│   ├── usage_store.py      # SQLite per-call token/cost ledger
│   └── skills.py           # Skill manifest and loader
├── skills/                 # Drop-in skills (each a dir with SKILL.md)
├── portal/                 # Standalone FastAPI portal app (separate project)
├── eval/                   # LLM-graded eval suites and harness
├── onboarding/             # First-run identity scaffolding
├── context/                # Inputs supplied to the agent (mounted/configured)
│   ├── identity.md         # Assistant persona and instructions
│   ├── memory/             # Persistent markdown memory store
│   ├── input/              # Drop-zone for user-supplied input files
│   └── schedules.json      # Cron tasks evaluated by scheduler
├── workspace/              # Gitignored runtime volume — outputs the agent produces
│   ├── generated/          # Generated deliverables (research reports, memos, PDFs)
│   └── scratch/            # Transient/intermediate files (safe to delete)
└── Dockerfile              # Container with Python 3.12, ripgrep, git
```

The directory split tracks file provenance: `context/` holds everything
supplied **to** the agent (persona, memory, scheduled tasks, user-dropped
input files), while `workspace/` is the gitignored runtime volume holding
everything the agent **produces** — generated deliverables under
`generated/` and transient working files under `scratch/`. Skills writing
files to disk follow this convention; see `src/tools/README.md` for the
full output-path rules.

## Quick Start

### Local

```bash
git clone https://github.com/jalemieux/curunir.git
cd curunir
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your API key
vim context/identity.md     # define your assistant's persona
python run.py               # starts CLI channel
```

### Docker (local dev)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The dev override re-adds `build:` for both services, bind-mounts
`./portal` into the portal container, and enables uvicorn `--reload`.

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
identically over the CLI WebSocket and the portal browser UI. Channels
forward slash text to `agent_worker` as `command="slash"` messages —
dispatch happens there so channels stay ignorant of the skill registry.
Cancellation is the only slash-adjacent action handled channel-side, as
an out-of-band `{"command": "interrupt"}` frame (bound to Ctrl-C in the
CLI), because the agent worker is blocked during a turn and can't drain
the queue in time.

#### Email Channel (deadsimple.email)

The email channel uses [deadsimple.email](https://deadsimple.email) — an HTTP API for sending and receiving mail. Create an inbox and an API key in the deadsimple dashboard, then set:

```bash
EMAIL_ENABLED=true
DEADSIMPLE_API_KEY=dse_your_api_key
DEADSIMPLE_INBOX_ID=<inbox-uuid>
EMAIL_ALLOWED_SENDERS=alice@example.com,bob@example.com
```

The channel polls every `EMAIL_POLL_INTERVAL` seconds (default 60). Replies use deadsimple's `/reply` endpoint when text-only, or `/messages` with explicit threading headers when attachments are included. Inbound mail with `is_spam=true` or `spam_score >= EMAIL_SPAM_SCORE_THRESHOLD` (default 5.0) is dropped. The discovery cursor is persisted to `./context/email_state.json` so restarts resume without reprocessing history.

Delivery is decoupled from discovery so an outbound failure can't silently drop a message. Every inbound is recorded in a durable **pending-reply ledger** in the same state file the moment it is queued, and only cleared once its reply is confirmed sent. If a reply send fails (DNS/network/5xx outage), the computed reply is stored and re-sent with exponential backoff (`EMAIL_SEND_RETRY_BACKOFF`, default 30s) up to `EMAIL_SEND_MAX_RETRIES` (default 5) attempts before being dead-lettered and escalated at ERROR; `EMAIL_FAILURE_ALERT_THRESHOLD` (default 5) consecutive failures also escalates. On restart the ledger is re-driven (unanswered inbound re-enqueued, failed sends re-sent). A genuine first run skips pre-existing mail, but a *corrupt* state file is never fast-forwarded — the channel alerts and waits for an operator to repair or remove it.

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

A lightweight, operator-only web console served directly from the curunir container — the co-located counterpart to the hosted portal. Where the portal relays chat through a remote service, this UI reads the container-local stores *directly*: token/cost usage (`context/usage.db`), the balance sheet (`context/memory/portfolio.db`), scheduled tasks (`context/schedules.json`), and the `context/memory/` tree. It reuses the portal chat frontend and wire protocol but bridges `/ws/browser` straight into the local agent queues.

Off by default. Enable it with:

```bash
LOCAL_UI_ENABLED=true
# LOCAL_UI_HOST=127.0.0.1   # 0.0.0.0 inside Docker (compose sets this)
# LOCAL_UI_PORT=8766
```

It binds loopback and reuses the WS channel's `context/.ws-token` pairing token and Origin allowlist — no separate auth. Open it at `http://localhost:8766/?token=<token>` (the token is printed in the startup log and stored in `context/.ws-token`). v1 is **read-only panels + chat**; edits stay with the existing tools/skills.

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

Skills can declare opt-in tools that are only available when the skill is loaded. Add a `tools` field to the frontmatter:

```yaml
---
name: deep-research
description: Research a topic in depth
tools: attach
---
```

When the agent loads this skill via `load_skill`, the listed tools are added to the agent's tool set for the remainder of the session. This keeps the default tool set lean while allowing skills to unlock capabilities they need.

**Available opt-in tools:**

| Tool | Description |
|------|-------------|
| `attach` | Attach a file to the agent's response. Delivered as an email attachment, CLI file path, etc. depending on channel. |

## Personas

A persona is a deployment bundle: an optional absolute skill allowlist and a
`prompts/` directory layered on top of `context/identity.md` in the system
prompt. Select one with `CURUNIR_PERSONA=<name>`. Unset falls back to
`personas/default/`, which ships the full skill catalog and the baseline
behavior prompt — so unset is itself a persona, not a special case.

The shipped specialty example is `finance` — a local, private personal-finance
assistant that curates skills down to analysis/memo/data tools and adds
domain + guardrails prompts. See `personas/finance/README.md`.

```bash
cp personas/finance/.env.finance.example .env
CURUNIR_PERSONA=finance python run.py
```

## Evals

LLM-graded eval harness in `eval/` that sends prompts to Curunir over WebSocket and records results.

```bash
# Run basic evals (tool use, planning, memory, instruction following)
python eval/run_evals.py

# Run advanced evals (web search, deep research, delegation, cross-skill orchestration)
python eval/run_evals.py --file eval/advanced_evals.md

# Cap iterations per prompt
python eval/run_evals.py --max-loops 20

# Against a remote instance
python eval/run_evals.py --host myserver.example.com --port 8765
```

Results are saved to `eval/eval_results/` as timestamped JSON files including the model name, all prompts, responses, and tool calls.

- `eval/simple_evals.md` — prompts testing core capabilities (no API keys needed)
- `eval/advanced_evals.md` — prompts testing skills like web-search, deep-research, and delegation (requires `BRAVE_API_KEY` and network access)

## Configuration

Configuration is handled via `src/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `model` | `anthropic/claude-sonnet-4-20250514` | LLM model (any LiteLLM-supported model) |
| `max_iterations` | `75` | Max tool-calling rounds per turn |
| `max_history_chars` | `250000` | Conversation history limit; lower for small-context models |
| `identity_file` | `./context/identity.md` | Path to persona file |
| `context_dir` | `./context` | Path to context directory (memory, etc.) |
| `skill_dirs` | `[./skills, ./context/skills]` | Directories scanned for skills in priority order (first-seen wins on name collision) |

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
