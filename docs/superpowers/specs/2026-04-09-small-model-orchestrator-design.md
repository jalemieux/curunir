# Small-Model Orchestrator — Design Spec

## Overview

Adapt Curunir to run on extremely constrained local hardware using a small quantized LLM (Gemma 4 26B MoE, Q4_K_M) with limited context window and slow token generation. The core idea: replace the single-agent architecture with an orchestrator that delegates to specialized sub-agents, each running in a fresh, minimal context.

**Branch:** `small-model` — isolated from main until validated.

**Target hardware:** AMD Phoenix APU (Ryzen 7000/8000, Radeon 780M iGPU), unified memory, running llama.cpp.

**Benchmarked performance:**

| Backend | pp (tok/s) | tg (tok/s) | Max context |
|---------|-----------|-----------|-------------|
| Vulkan (iGPU) | ~314 | ~20 | ~4096 tokens (OOM at 8192) |
| CPU (`-ngl 0`) | ~144 | ~14.3 | 8192+ tokens |

**Practical constraint:** ~8k token context window on CPU, ~4k on Vulkan. System prompt overhead must be minimized to leave room for actual work.

## Architecture

```
User ↔ CLI (WebSocket, async) ↔ Orchestrator Agent
                                      │
                    ┌─────────────────┬┴────────────────┐
                    ▼                 ▼                  ▼
              Files Agent      System Agent      Web Agent    ...
              (glob,grep,     (bash)            (web_fetch)
               read,edit,
               write)
```

### Orchestrator

A lightweight routing agent. Its job: understand user intent, delegate to the right specialist, manage conversation continuity, and answer simple questions directly (no delegation needed when no tools are required).

**System prompt (~300 tokens):**

```
You are {{name}}, a personal assistant running on local hardware.

You can answer simple questions directly. For tasks requiring tools, delegate to a specialist.

## Specialists
| Agent     | Use for                                |
|-----------|----------------------------------------|
| files     | Read, edit, write, search files        |
| system    | Shell commands, services, system info  |
| web       | Fetch URLs                             |
| email     | Read and send email                    |
| scheduler | Manage recurring tasks                 |
| memory    | Store and recall persistent facts      |

## Rules
- Delegate by calling the delegate tool with an agent name and a concise task description.
- Include all context the specialist needs in the task — they have no memory of this conversation.
- For multi-step tasks, delegate one step at a time and use each result to inform the next.
- After each delegation, summarize the result to the user in 1-2 sentences.
- If no tools are needed, respond directly.

Current time: {{isotimestamp}}
```

**Tools:** Only `delegate`.

**History cap:** ~16,000 chars (~4k tokens), configurable via `MAX_HISTORY_CHARS`.

### Sub-agents

Each sub-agent is a fresh `Agent` instance spawned per delegation. Defined declaratively in `context/agents.yaml`:

```yaml
files:
  description: "File operations — read, edit, write, search"
  tools: [glob, grep, read, edit, write]
  system_prompt: >
    You are a file operations specialist. Complete the task below.
    Report what you did in under 100 words.
    Do not explain your reasoning. Just do the task and report the result.
  max_iterations: 10

system:
  description: "Shell commands and system management"
  tools: [bash]
  system_prompt: >
    You are a system operations specialist. Run commands to complete the task.
    Report output concisely.
  max_iterations: 10

web:
  description: "Fetch and process web content"
  tools: [web_fetch]
  system_prompt: >
    You are a web research specialist. Fetch the requested information
    and summarize it concisely.
  max_iterations: 5

email:
  description: "Read and send email"
  tools: [email_read, email_send]
  system_prompt: >
    You are an email specialist. Complete the email task and report what you did.
  max_iterations: 5

scheduler:
  description: "Manage recurring tasks"
  tools: [schedule]
  system_prompt: >
    You are a scheduling specialist. Create, modify, or report on scheduled tasks.
  max_iterations: 5

memory:
  description: "Store and recall information across sessions"
  tools: [read, write, glob]
  system_prompt: >
    You are a memory specialist. Read from or write to the memory directory
    at context/memory/. Report what you found or stored concisely.
  max_iterations: 5
```

**Sub-agent properties:**
- Fresh context per invocation — no carry-over between delegations
- Low iteration caps (5-10) — forces concise execution, fails fast
- Minimal system prompts (~60 tokens) — one-liner persona, no identity.md, no skill manifest
- Cannot delegate further — no `delegate` tool available
- Context budget: ~400-600 tokens overhead (prompt + 2-5 tool schemas), leaving ~7,400-7,600 tokens for work

### Delegate Tool (modified)

```json
{
  "name": "delegate",
  "description": "Delegate a task to a specialist agent",
  "parameters": {
    "agent": {
      "type": "string",
      "enum": ["files", "system", "web", "email", "scheduler", "memory"],
      "description": "Which specialist to delegate to"
    },
    "task": {
      "type": "string",
      "description": "Concise task description with all necessary context"
    }
  }
}
```

**Execution flow:**

1. Orchestrator calls `delegate(agent="files", task="Check nginx.conf for syntax errors and fix them")`
2. Dispatcher looks up `files` in `agents.yaml` — gets tools, system prompt, iteration cap
3. Spawns a new `Agent` with those constraints
4. Sub-agent runs to completion (or hits iteration/timeout cap)
5. Sub-agent's final response is truncated to ~500 tokens if needed (hard safety net)
6. Result returned to orchestrator
7. Orchestrator synthesizes a one-line summary for its history, sends a human-friendly response to the user

**Multi-agent tasks:** The orchestrator handles chaining. "Find the largest log file and email me its contents" becomes: delegate to files first, get the result, then delegate to email with the relevant info included in the task prompt. The orchestrator does the reasoning about sequencing; sub-agents stay focused.

**Error handling:** If a sub-agent fails (iteration cap, tool error, context overflow), it returns whatever it has. The orchestrator can retry with a simpler task prompt, try a different agent, or report the failure to the user. No silent swallowing.

## Execution Model

### Synchronous Core, Async Edges

**Why synchronous:**
1. The model can't handle concurrent calls — single llama.cpp process, one inference at a time
2. Sequential execution helps the orchestrator reason about multi-step task ordering
3. No batching or concurrent KV caches competing for memory — full RAM budget goes to one context at a time

**What's synchronous:**
- Tool execution: one tool call at a time, blocking, in order
- Delegate calls: orchestrator waits for sub-agent to fully complete before next decision
- LLM calls: one inference at a time. Sub-agent running = orchestrator idle.

**What stays async:**
- WebSocket channel listener (accepting incoming messages)
- Outbound message router (sending responses back)
- The core agent loop itself can remain an async function for compatibility — it just awaits each step sequentially rather than gathering concurrent tasks

## Context & History Management

### Orchestrator History

**Summary compaction:** After each completed delegation, the orchestrator's own response to the user serves as the summary. The raw delegate exchange (tool call message + tool result message) is dropped from history, leaving only the orchestrator's human-facing summary. Example:

```
Before compaction:
  [assistant] tool_call: delegate(agent="system", task="Run nginx -t...")
  [tool]      "nginx: [emerg] unexpected ';' in /etc/nginx/sites-enabled/default:42..."
  [assistant] "There's a syntax error on line 42..."

After compaction:
  [summary] [system] nginx -t showed syntax error on line 42 of /etc/nginx/sites-enabled/default
  [assistant] "There's a syntax error on line 42..."
```

**Trim strategy:** When history exceeds the 16k char cap, drop oldest summary+exchange pairs first. Always preserve the most recent user message.

**Example conversation in history:**

```
[user] Check if nginx has errors and fix them
[assistant] I'll have the system agent check that.
[summary] [system] nginx -t showed error on line 42 of /etc/nginx/sites-enabled/default
[summary] [files] Fixed missing semicolon on line 42 of /etc/nginx/sites-enabled/default
[assistant] Fixed — there was a missing semicolon on line 42. nginx -t passes now.
[user] Great, restart nginx
[assistant] I'll have the system agent do that.
```

### Sub-agent Context

- No history management — start fresh, die after one task
- Context is: system prompt + tool schemas + task prompt + own tool call/result pairs
- If a sub-agent overflows mid-task (e.g., `read` returns a huge file), it fails and reports back. The orchestrator can retry with a more specific task.

### Memory

Auto-extraction (today's `memory_extractor.py`) is **disabled**. Memory becomes an explicit sub-agent:
- User says "remember that the nginx config is at /etc/nginx/sites-enabled/default"
- Orchestrator delegates to memory agent
- Memory agent writes to `context/memory/` using read/write/glob tools
- No LLM-based extraction, no replaying conversation history

## System Prompts

### Design Principles

- Every token counts. No filler, no verbose instructions.
- Sub-agent prompts explicitly say "do not explain your reasoning" — prevents the model from burning tokens on chain-of-thought narration.
- No identity.md blob — replaced by one-line name injection in orchestrator prompt.
- No skill manifest — skills system is disabled entirely in small-model mode.
- No memory taxonomy or extraction prompts.

### Token Budget Summary

| Component | Tokens |
|-----------|--------|
| Orchestrator system prompt | ~300 |
| Orchestrator `delegate` tool schema | ~100 |
| **Orchestrator total overhead** | **~400** |
| Sub-agent system prompt | ~60 |
| Sub-agent tool schemas (2-5 tools) | ~200-500 |
| Task prompt from orchestrator | ~100-300 |
| **Sub-agent total overhead** | **~400-860** |

In an 8k token window: orchestrator gets ~7,600 tokens for conversation. Sub-agents get ~7,100-7,600 tokens for work.

## UX Design

### Task-Oriented Framing

CLI welcome message sets expectations:

```
Connected to {{name}} (local mode)
Tip: I work best with focused requests. Ask me to do something specific.
```

### Context Usage Indicator

A 5-block bar showing orchestrator context fill level:

```
[ctx: ██░░░] you> restart nginx and check if the site is up
```

At 4/5 blocks, indicator turns yellow. At 5/5, next message triggers history trimming. Gives the user a natural nudge to wrap up or start fresh.

### Topic Reset

```
you> /clear
Context cleared. Starting fresh.
```

Wipes orchestrator history. Persistent memory (files in `context/memory/`) survives.

### Delegation Progress

Since delegations take 10-15 seconds, show status:

```
you> check if nginx config has errors
[delegating to system...] ████░░░░ running nginx -t
```

Spinner or progress bar so the user knows it's working, not hung.

## What's Cut From Mainline Curunir

| Feature | Status in small-model mode |
|---------|---------------------------|
| Skills system (`load_skill`, manifest) | Disabled entirely |
| Memory auto-extraction | Disabled — memory is an explicit sub-agent |
| Concurrent tool execution | Disabled — all sequential |
| Large identity/persona files | Replaced by one-line name in orchestrator prompt |
| 75-iteration agent loop | Replaced by 5-10 per sub-agent |
| 250k char history limit | Replaced by 16k char with summary compaction |
| `delegate` (generic) | Replaced by targeted delegation to named agents |

## What's New

| Component | Description |
|-----------|-------------|
| `context/agents.yaml` | Declarative sub-agent definitions |
| Modified `delegate` tool | Takes `agent` parameter, looks up config |
| Summary compaction | Replaces delegate exchanges with one-line summaries in history |
| Result truncation | Hard cap at ~500 tokens on sub-agent responses |
| Context indicator | CLI shows context fill level |
| `/clear` command | Explicit topic reset |
| Delegation progress | CLI shows sub-agent status during execution |

## Open Questions

1. **Vulkan vs CPU mode selection** — should this be a config flag, or should Curunir auto-detect based on available VRAM and fall back to CPU? For now, likely a config flag (`BACKEND=cpu|vulkan`), since llama.cpp handles this at the server level, not in Curunir.
2. **Sub-agent roster** — the 6 agents listed here are a starting point. Should be easy to add/remove via `agents.yaml` without code changes. The orchestrator prompt's specialist table should be auto-generated from the yaml.
3. **Result truncation strategy** — hard truncation at 500 tokens is a safety net. In practice, the "report concisely" instruction should keep most responses under that. May need tuning after testing with the actual model.
