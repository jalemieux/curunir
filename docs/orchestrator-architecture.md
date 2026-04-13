# Curunir Orchestrator Architecture

Curunir's orchestrator mode is designed for systems with limited VRAM and unified memory. Instead of a single powerful agent, a lightweight routing agent delegates tasks to specialized sub-agents, each running in a fresh, minimal context.

## Orchestrator Architecture

```
User <-> CLI <-> Orchestrator Agent (delegate tool only)
                        |
          +-------------+-------------+
          v             v             v
    Files Agent   System Agent   Web Agent   ...
    (glob,grep,   (bash)         (web_fetch)
     read,edit,
     write)
```

The **orchestrator** is a lightweight routing agent. It understands user intent and delegates to the right specialist. Its system prompt is ~300 tokens — just a name, a table of specialists, and delegation rules.

Each **sub-agent** is a fresh `Agent` instance spawned per delegation with:
- A minimal system prompt (~60 tokens)
- Only the tools it needs (2-5 tool schemas)
- A low iteration cap (5-10)
- No conversation history, no skills, no identity file

## Context Budget

At startup, Curunir queries llama.cpp's `/slots` endpoint for `n_ctx` (the model's real context window in tokens). After every LLM call, the response's `usage.prompt_tokens` reports exactly how many tokens were in the window. When that crosses 85% of `n_ctx`, Curunir trims the oldest half of the conversation before the next call. On a hard `ContextWindowExceededError`, the same halving runs reactively and the call is retried once.

There are no char-based heuristics, no `CHARS_PER_TOKEN` magic, and no manual `MAX_HISTORY_CHARS` env var — everything follows the model's own tokenizer.

Sub-agents inherit the same `n_ctx` and reuse the same trim policy. They tend to stay well below the threshold because each sub-agent runs in a fresh context with a small task and a tight iteration cap.

## Summary Compaction

After each delegation, the orchestrator compacts the raw tool call exchange into a one-line summary:

```
Before: [assistant tool_call] + [tool result: 500 chars] = ~600 chars
After:  [summary] [system] uptime: 5 days = ~30 chars
```

This keeps the orchestrator's history small so conversations can last longer within the tight context window.

## What's Disabled

In orchestrator mode, these features are turned off to save context and avoid complexity:

- **Skills system** — no `load_skill`, no skill manifest in the prompt
- **Automatic memory extraction** — memory is an explicit sub-agent instead
- **Concurrent tool execution** — one tool at a time (single inference process)
- **Large identity files** — replaced by a one-line name injection

## Customizing Sub-Agents

Sub-agents are defined in `context/agents.yaml`. The default configuration ships with six specialists:

| Agent | Tools | Use for |
|-------|-------|---------|
| `files` | glob, grep, read, edit, write | File operations |
| `system` | bash | Shell commands |
| `web` | web_fetch | Fetching URLs |
| `email` | email_read, email_send | Email |
| `scheduler` | schedule | Cron tasks |
| `memory` | read, write, glob | Persistent facts |

### Adding or modifying agents

Edit `context/agents.yaml`:

```yaml
my-agent:
  description: "What this agent does (shown to orchestrator)"
  tools: [tool1, tool2]
  system_prompt: >
    You are a specialist. Do the task. Report concisely.
  max_iterations: 5
```

The orchestrator's specialist table and the delegate tool's `agent` enum are auto-generated from this file at startup. No code changes needed.

### Writing effective sub-agent prompts

- Keep prompts under 100 tokens
- Say "do not explain your reasoning" to prevent chain-of-thought narration that burns context
- Say "report in under 100 words" to keep results compact
- Be specific about the agent's role so it stays focused
