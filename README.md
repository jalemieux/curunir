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
│   ├── channels/           # CLI/WS, Email, Portal channels and router
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
├── context/
│   ├── identity.md         # Assistant persona and instructions
│   ├── memory/             # Persistent markdown memory store
│   └── schedules.json      # Cron tasks evaluated by scheduler
└── Dockerfile              # Container with Python 3.12, ripgrep, git
```

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

### Docker

```bash
docker compose up --build
```

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

The channel polls every `EMAIL_POLL_INTERVAL` seconds (default 60). Replies use deadsimple's `/reply` endpoint when text-only, or `/messages` with explicit threading headers when attachments are included. Inbound mail with `is_spam=true` or `spam_score >= EMAIL_SPAM_SCORE_THRESHOLD` (default 5.0) is dropped. The polling watermark is persisted to `./context/email_state.json` so restarts resume without reprocessing history.

See `.env.example` for the full list of email-related variables.

#### Portal Channel (hosted web UI)

The portal is a standalone FastAPI app (in `portal/`) that gives the agent a multi-user browser front end with email-link sign-in, per-tab sessions, and drag-drop attachments. The curunir container dials *out* to the portal over WebSocket on startup; the portal multiplexes each browser to the matching container.

Enable it by setting:

```bash
CURUNIR_PORTAL_URL=wss://your-portal.example.com/ws/agent
CURUNIR_PORTAL_TOKEN=<bearer-token-issued-by-portal>
```

See **[portal/README.md](portal/README.md)** for portal deployment and the local `docker compose --profile portal up` dev path.

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
