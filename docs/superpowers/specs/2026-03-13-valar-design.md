# Valar — Configurable Agent Framework

## Overview

Valar is a general-purpose agent framework that can be customized for various end uses (personal assistant, marketer, financial analyst, etc.) through configuration, identity files, and deployment-specific skills. One profile per deployment.

Lessons learned from three prior projects (playbook, playbook_3, assistant_adobe) are distilled into this design.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Docker Container                   │
│                                                      │
│  config.yaml ─→ Startup                              │
│                    │                                  │
│         ┌──────────┼──────────┐                      │
│         ▼          ▼          ▼                       │
│     CLI Chan   Slack Chan  Email Chan                │
│         │          │          │                       │
│         ▼          ▼          ▼                       │
│       ┌────── InQueue ───────┐                       │
│       │  (channel, session,  │                       │
│       │   reply_address,     │                       │
│       │   content)           │                       │
│       └─────────┬────────────┘                       │
│                 ▼                                     │
│           Agent Loop (sequential)                    │
│      (system_prompt = identity.md                    │
│       + skill manifest + timestamp)                  │
│           │          │                               │
│           ▼          ▼                               │
│        7 Tools    LLM (LiteLLM)                     │
│                      │                               │
│                      ▼                               │
│           Memory Extractor                           │
│           (post-conversation)                        │
│                      │                               │
│       ┌──────────────▼─────────────┐                 │
│       │         OutQueue           │                 │
│       │  (routed by channel +      │                 │
│       │   reply_address)           │                 │
│       └───┬──────────┬──────────┬──┘                 │
│           ▼          ▼          ▼                     │
│        CLI Out   Slack Out  Email Out                │
└─────────────────────────────────────────────────────┘
```

## Runtime

- Single Python process in Docker
- All channels run as async tasks in one event loop
- Agent loop processes messages sequentially from InQueue
- No sub-agents, no concurrent session processing

## Agent Loop

Synchronous loop, proven across all three predecessor projects:

```
handle(message, session_id):
    1. Append user message to session history
    2. Build messages = [system_prompt] + session_history
    3. Loop (max_iterations):
       a. Call LLM (messages + tool schemas)
       b. If text response → append to history, return response
       c. If tool_calls → execute each, append results, continue
    4. If max_iterations hit → return "iteration limit reached"
```

### System Prompt

Built at startup from:
- `identity.md` content (personality, capabilities, memory instructions)
- Skill manifest (table of skill names + activation triggers)
- Current timestamp and timezone

### Sessions

In-memory dict: `{session_id: [messages]}`

Session IDs determined by channel:
- CLI: `"cli"` (reset on process exit)
- Slack: `thread_ts`
- Email: message thread ID

Each conversation is a separate session. Sessions are not persisted — they live in memory only.

### Memory Extraction

After each agent turn that produces substantive content:
1. Separate LLM call with the conversation + extraction prompt
2. LLM identifies durable facts (filter: "will this still be true in 6 months?")
3. Writer appends facts to appropriate memory category files (creates if needed)
4. Conversation summary saved to `archives/conversations/YYYY-MM-DD-topic.md`

Greetings and trivial exchanges are skipped.

## Tools

Seven tools. All are simple executors that return strings. Errors return error strings, not exceptions.

| Tool | Args | Description |
|------|------|-------------|
| `Glob` | `pattern, path?` | Find files by pattern (Python glob) |
| `Grep` | `pattern, path?, glob?, output_mode?, context?` | Regex content search via ripgrep |
| `Read` | `file_path, offset?, limit?` | Read file with line numbers |
| `Edit` | `file_path, old_string, new_string, replace_all?` | Exact string replacement |
| `Write` | `file_path, content` | Create or overwrite file |
| `Bash` | `command, timeout?` | Shell command execution (default 30s timeout) |
| `load_skill` | `name` | Load skill instructions by name |

### Dispatcher

```python
EXECUTORS = {
    "glob": exec_glob,
    "grep": exec_grep,
    "read": exec_read,
    "edit": exec_edit,
    "write": exec_write,
    "bash": exec_bash,
    "load_skill": exec_load_skill,
}

def execute_tool_call(name, args, config) -> str:
    executor = EXECUTORS.get(name.lower())
    return executor(args, config)
```

Each tool has a JSON schema in OpenAI function-calling format. New tools are added by: (1) define schema, (2) write executor, (3) register in dispatcher.

## Skills

### Structure

```
skills/
├── skill-a/
│   └── SKILL.md
├── skill-b/
│   └── SKILL.md
```

### SKILL.md Format

```yaml
---
name: skill-name-with-hyphens
description: Use when [triggering conditions only, not what it does]
---

# Skill Name

## Overview
Core principle in 1-2 sentences.

## When to Use
- Symptom/trigger bullets
- When NOT to use

## Quick Reference
Table or bullets for scanning.

## Instructions
Step-by-step workflow using core tools.

## Common Mistakes
What goes wrong + fixes.
```

### Key Rules

- `description` field states triggering conditions only (so the agent knows *when* to load, not *what* it does)
- Keep skills lean: <500 words for most, <200 for frequently-loaded
- Skills are behavioral recipes — they describe *how* to achieve something using the 7 core tools
- Heavy reference material goes in separate files alongside SKILL.md
- Flat namespace, hyphenated names

### Mechanics

1. Startup: scan `skills/*/SKILL.md`, parse YAML frontmatter + "When to Activate" section
2. Build manifest table (name + triggers), inject into system prompt
3. Agent calls `load_skill(name)` when intent matches a trigger
4. Skill content returned as tool result, agent follows instructions

No built-in skills shipped. Each deployment creates its own.

## Memory

### Structure

```
context/memory/
├── README.md              # Taxonomy, naming conventions, usage flow (entry point)
├── projects/              # Active initiatives (goals, timelines, stakeholders)
├── systems/               # Technical infrastructure, services, APIs
├── decisions/             # Architectural choices with rationale (YYYY-MM-topic.md)
├── people/                # Contacts, colleagues, teams
├── tasks.md               # Open items needing resolution
├── preferences.md         # User's working style and preferences
└── archives/
    └── conversations/     # Post-conversation summaries (YYYY-MM-DD-topic.md)
```

### README.md

The memory README serves as the index and orientation document. It contains:
- Category taxonomy with purpose and examples
- File naming conventions
- Usage flow (how the agent should search memory)

### Identity References Memory

`identity.md` points to `context/memory/README.md` as the starting point:

```markdown
## Memory
You have a persistent memory system in `context/memory/`.
**START HERE:** Read `context/memory/README.md` first.
```

### Memory Lookup Flow (baked into identity.md)

1. Check `context/memory/README.md` for orientation
2. Search appropriate category with `Grep`
3. Read specific files for detail
4. Only if no results, ask the user

### Auto vs Manual

- **Auto:** Post-conversation extraction writes durable facts to category files and archives conversation summaries
- **Manual:** Agent can read/write memory files directly when asked or when a skill instructs it (corrections, explicit "remember X" requests, task updates)

## Channels & Queue

### Message Types

```python
@dataclass
class IncomingMessage:
    content: str
    channel: str         # "cli", "slack", "email"
    session_id: str      # determined by channel
    reply_address: dict  # channel-specific routing info

@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
```

### Queue

Two `asyncio.Queue` instances: inbound and outbound. Channels are async tasks that push to inbound and pull from outbound. Agent loop pulls from inbound, processes sequentially, pushes response to outbound.

### Channel Details

| Channel | Session ID | Reply Address | I/O Method |
|---------|-----------|---------------|------------|
| CLI | `"cli"` | stdout | stdin loop |
| Slack | `thread_ts` | `{channel_id, thread_ts}` | Socket mode |
| Email | thread ID | `{to, subject, in_reply_to}` | `gog` CLI tool via bash |

### Routing

Outbound router reads `channel` + `reply_address` from the outgoing message and dispatches to the correct channel's send method. Each channel handles its own output formatting (e.g., Slack markdown conversion).

### CLI Output

Rich feedback following the playbook pattern:
- Spinner while the agent is thinking
- Tool call summaries (collapsed by default)
- Verbose mode toggle (`/verbose`) for full tool output
- `/clear` or `/new` to reset session

## Identity

`context/identity.md` is the agent's system prompt. It defines:

- **Persona:** Name, role, core traits
- **Voice:** Communication style, tone guidelines
- **Capabilities:** What the agent can do
- **Memory section:** Points to `context/memory/README.md`, defines lookup flow, explains auto-capture
- **Guidelines:** Response protocol, when to ask vs act

Each deployment customizes this file for its use case. The framework provides no default identity — it must be configured.

## Configuration

### config.yaml

```yaml
agent:
  model: anthropic/claude-sonnet-4-20250514
  max_iterations: 15
  identity_file: ./context/identity.md
  context_dir: ./context
  skills_dir: ./skills

channels:
  cli:
    enabled: true
  slack:
    enabled: false
    app_token: ${SLACK_APP_TOKEN}
    bot_token: ${SLACK_BOT_TOKEN}
  email:
    enabled: false
    poll_interval_sec: 60

log_level: INFO
```

### Environment Variables

- `${VAR}` interpolation in config.yaml
- `.env` file loaded at startup, values stored in memory, removed from `os.environ`
- `.env.example` documents required variables per channel

## Project Structure

```
valar/
├── main.py                     # Entry point, startup, channel launcher
├── config.yaml
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── context/
│   ├── identity.md
│   └── memory/
│       ├── README.md
│       ├── projects/
│       ├── systems/
│       ├── decisions/
│       ├── people/
│       ├── tasks.md
│       ├── preferences.md
│       └── archives/
│           └── conversations/
├── skills/
├── src/
│   ├── agent/
│   │   ├── agent.py            # Agentic loop, session management
│   │   ├── system_prompt.py    # Build prompt from identity + skills
│   │   └── memory_extractor.py # Post-conversation fact extraction
│   ├── tools/
│   │   ├── dispatcher.py       # Tool registry + execute_tool_call
│   │   ├── schemas.py          # All 7 tool JSON schemas
│   │   ├── fs_tools.py         # Glob, Grep, Read, Edit, Write
│   │   ├── bash_tool.py        # Bash execution
│   │   └── skill_tool.py       # load_skill executor
│   ├── channels/
│   │   ├── base.py             # Message types (Incoming/Outgoing)
│   │   ├── router.py           # Outbound routing
│   │   ├── cli.py              # CLI with spinner, verbose mode
│   │   ├── slack.py            # Slack socket mode
│   │   └── email.py            # Email via gog CLI
│   ├── queue.py                # InQueue + OutQueue (asyncio.Queue)
│   ├── skills.py               # Skill scanner, manifest builder
│   ├── config.py               # YAML loader with ${ENV_VAR} interpolation
│   ├── logger.py               # Centralized logging
│   └── llm.py                  # LiteLLM wrapper
└── tests/
```

## Dependencies

- `litellm` — multi-provider LLM access
- `pyyaml` — config loading
- `python-dotenv` — .env file loading
- `slack-bolt` — Slack socket mode (when Slack channel enabled)
- Standard library: `asyncio`, `subprocess`, `glob`, `re`, `json`, `pathlib`

## What This Design Excludes

- Sub-agents / delegation
- Persistent conversation storage (sessions are in-memory only)
- Web fetch tool (use `curl` via Bash)
- Built-in skills (each deployment brings its own)
- Multi-profile support (one profile per deployment)
- Concurrent session processing (sequential only)
