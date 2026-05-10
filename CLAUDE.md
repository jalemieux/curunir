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

`Agent.handle()` is the heart: receive message → trim history (250k char limit) → build system prompt → call LLM (via LiteLLM) → execute tool calls sequentially → loop (max 75 iterations) → return response.

Context overflow is caught from LiteLLM exceptions; history is adaptively trimmed to 125k chars and retried.

**Cancellation.** `Agent.request_cancel(session_id)` sets a per-session `asyncio.Event` that the loop checks at the top of each iteration and before each tool call within a batch. Channels call this out-of-band when the user requests a stop (the in_queue is blocked while `handle()` runs). The in-flight LLM call and the currently-executing tool run to completion, but any remaining tool calls in the batch are skipped and stubbed with an `(interrupted)` tool response so every `tool_call_id` has a matching response (chat schemas require this). On the next iteration the outer cancel check fires, an `(interrupted)` assistant turn is appended, and `handle()` returns `"(interrupted)"`.

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

- **WebSocket** (`ws.py`): Primary CLI interface on port 8765. Server-issued per-connection session id (resumable via the hello frame). When `WS_AUTH_TOKEN` is set, the very first inbound frame must be `{"type": "hello", "token": "..."}` (compared with `hmac.compare_digest`); pre-auth rejections close 1008 and don't enqueue an `extract`. `WS_HOST=""` disables the listener. Startup refuses to bind a non-loopback host without a token.
- **Email** (`email.py`): Gmail via Google Workspace service account. Session ID is thread ID. Polls inbox every 60s.
- **Portal** (`portal.py`): Outbound WebSocket to a hosted portal (`CURUNIR_PORTAL_URL` + `CURUNIR_PORTAL_TOKEN`). Container dials portal; portal multiplexes browser ↔ container. Session ID is `"portal"`. See `portal/` directory for the portal service.
- **Router** (`router.py`): Routes outgoing messages back to the originating channel.

Channels implement a protocol: `async start()` to listen, `async send(msg)` to respond.

**Interrupts.** WS and Portal channels accept an optional `cancel_session=agent.request_cancel` callback. When the client sends `{"command": "interrupt"}`, the channel routes it directly to the callback instead of enqueuing it (the agent_worker is blocked inside `handle()` and wouldn't drain the queue in time). The CLI (`cli.py`) wires Ctrl-C to send this frame while the agent is busy, via `loop.add_signal_handler(SIGINT, ...)`. While the prompt is active, prompt_toolkit reads Ctrl-C as a key in raw mode so the signal handler doesn't fire there — Ctrl-C at the prompt still exits.

### Tools (`src/tools/`)

**Default tools:** glob, grep, read, edit, write, bash, load_skill, web_fetch, delegate, schedule

**Opt-in tools** (unlocked when a skill requests them): attach

- Schemas registered in `schemas.py` via `_register()`
- Dispatch in `dispatcher.py` routes by name to executor functions
- Sync executors wrapped in `asyncio.to_thread()`; async executors awaited directly
- `delegate` spawns a sub-agent (sub-agents cannot delegate further)

See [`src/tools/README.md`](src/tools/README.md) for detailed documentation on the tool registry, dispatch pipeline, executor implementations, and how to add new tools.

### Skills (`src/skills.py`, `skills/`)

Each skill is a directory with a `SKILL.md` file using YAML frontmatter:
```yaml
---
name: my-skill
description: When to use this skill
tools: attach  # Optional: comma-separated opt-in tools
---
```

Manifest auto-built at startup from all `SKILL.md` files and included in the system prompt. Agent loads full skill content on demand via `load_skill` tool.

### Portal Service (`portal/`)

Standalone FastAPI app deployed to Render, separate Python project from the curunir container. See [`portal/README.md`](portal/README.md). Contains its own pyproject.toml, Dockerfile, render.yaml, and tests/. The curunir container talks to it via PortalChannel.

### Memory (`src/memory_extractor.py`)

Post-session, `extract_learnings()` calls the LLM with conversation history to extract facts → appends to markdown files in `context/memory/` → stores conversation summary in `context/memory/archives/conversations/`.

### Context Directory (`context/`)

Local directory containing `identity.md` (agent persona, required), `memory/` (persistent facts), and `schedules.json` (cron tasks). Use `sync-context.sh` to rsync from a remote machine before starting.

### Onboarding (`onboarding/`)

First-run scaffolding. New users fill `onboarding/questions.md`, then ask an LLM (curunir itself, Claude Code, etc.) to generate `context.default/identity.md` from those answers — `onboarding/README.md` has the prompt. `bootstrap.py` copies that file into `context/` on first launch (never overwriting existing files).

See [`onboarding/README.md`](onboarding/README.md) for the user-facing flow and the LLM generation prompt.

### Evals (`eval/`)

`python eval/run_evals.py` runs LLM-graded eval suites defined in `simple_evals.md` and `advanced_evals.md`. Supports `--max-loops` per prompt. Results written to `eval/eval_results/`.

### Scheduling (`src/scheduler.py`)

Cron tasks in `context/schedules.json` evaluated every second via croniter. When due, agent processes the task prompt via `handle()` in system-task mode.

## Testing Patterns

All tests are async (pytest-asyncio). Key fixtures in `tests/conftest.py`: `tmp_context`, `tmp_skills`, `agent_config`.

Mock LLM: `patch("src.agent.agent.call_llm", new_callable=AsyncMock)`

Key test files map 1:1 to modules: `test_agent.py`, `test_channels.py`, `test_tools.py`, `test_memory_extractor.py`, `test_scheduler.py`, etc.

## Key Environment Variables

See `.env.example` for full list. Critical ones:
- `MODEL` — LiteLLM format (e.g., `anthropic/claude-sonnet-4-20250514`)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY`
- `EMAIL_ENABLED`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_DELEGATED_USER`, `EMAIL_ALLOWED_SENDERS` — for email channel
- `MAX_HISTORY_CHARS` — conversation history limit in chars (default 250000; lower for small-context models)
- `WS_HOST` — bind interface for the WebSocket CLI listener (default `0.0.0.0`; set to `""` to disable the listener entirely)
- `WS_AUTH_TOKEN` — bearer token clients must present in their first hello frame; mandatory whenever `WS_HOST` is non-loopback
- `WS_ALLOWED_ORIGINS` — comma-separated browser Origin allowlist (default empty: only no-Origin clients accepted when auth is on)
- `CURUNIR_WS_TOKEN` — CLI client side; sent as the auth token in the first hello frame (also `cli.py --token`)
- `LOG_LEVEL` — set to `DEBUG` for detailed agent tracing
- `LOG_FILE` — path to a log file written via `RotatingFileHandler` (10MB × 3 backups). Docker compose sets this to `/app/workspace/curunir.log` so the introspection skill and `docker exec ... tail` can read agent activity. Unset → stderr only.
