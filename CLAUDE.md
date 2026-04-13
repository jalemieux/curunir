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

- **WebSocket** (`ws.py`): Primary CLI interface on port 8765. Session ID is fixed `"cli"`.
- **Email** (`email.py`): Gmail via Google Workspace service account. Session ID is thread ID. Polls inbox every 60s.
- **Router** (`router.py`): Routes outgoing messages back to the originating channel.

Channels implement a protocol: `async start()` to listen, `async send(msg)` to respond.

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

### Memory (`src/memory_extractor.py`)

Post-session, `extract_learnings()` calls the LLM with conversation history to extract facts → appends to markdown files in `context/memory/` → stores conversation summary in `context/memory/archives/conversations/`.

### Context Directory (`context/`)

Local directory containing `identity.md` (agent persona, required), `memory/` (persistent facts), and `schedules.json` (cron tasks). Use `sync-context.sh` to rsync from a remote machine before starting.

### Evals (`eval/`)

`python eval/run_evals.py` runs LLM-graded eval suites defined in `simple_evals.md` and `advanced_evals.md`. Supports `--max-loops` per prompt. Results written to `eval/eval_results/`.

### Scheduling (`src/scheduler.py`)

Cron tasks in `context/schedules.json` evaluated every second via croniter. When due, agent processes the task prompt via `handle()` in system-task mode.

### Orchestrator Mode (Small-Model)

Set `ORCHESTRATOR_MODE=true` for constrained local hardware. The agent becomes an orchestrator that delegates to specialized sub-agents defined in `context/agents.yaml`. Each sub-agent runs in a fresh context with minimal overhead. Skills and automatic memory extraction are disabled. See the design spec at `docs/superpowers/specs/2026-04-09-small-model-orchestrator-design.md`.

## Testing Patterns

All tests are async (pytest-asyncio). Key fixtures in `tests/conftest.py`: `tmp_context`, `tmp_skills`, `agent_config`.

Mock LLM: `patch("src.agent.agent.call_llm", new_callable=AsyncMock)`

Key test files map 1:1 to modules: `test_agent.py`, `test_channels.py`, `test_tools.py`, `test_memory_extractor.py`, `test_scheduler.py`, etc.

## Key Environment Variables

See `.env.example` for full list. Critical ones:
- `MODEL` — LiteLLM format (e.g., `anthropic/claude-sonnet-4-20250514`)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY`
- `EMAIL_ENABLED`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_DELEGATED_USER`, `EMAIL_ALLOWED_SENDERS` — for email channel
- `LOG_LEVEL` — set to `DEBUG` for detailed agent tracing
- `ORCHESTRATOR_MODE` — set to `true` for small-model orchestrator mode (delegates to sub-agents)

When `API_BASE` is set, Curunir reads the model's `n_ctx` from llama.cpp's `/slots` endpoint at startup and drives all trim decisions off real `prompt_tokens` reported on each call. For hosted models there is no proactive trim — overflow falls back to halving the message count and retrying.
