# Running Curunir on Local Hardware

Run Curunir on constrained local hardware using a small quantized LLM served by llama.cpp. Instead of a single powerful agent, Curunir switches to an **orchestrator mode** that delegates tasks to specialized sub-agents, each running in a fresh, minimal context.

## Hardware Requirements

Curunir's orchestrator mode is designed for systems with limited VRAM and unified memory. The reference hardware:

- **CPU:** AMD Ryzen 7000/8000 series (Phoenix APU)
- **GPU:** Integrated Radeon 780M iGPU (shared system memory)
- **RAM:** 32GB+ recommended (model + KV cache + OS)

Any system that can run a ~15GB quantized model via llama.cpp will work.

### Benchmarked Performance (Gemma 4 27B Q4_K_M)

| Backend | Prompt (tok/s) | Generation (tok/s) | Max context |
|---------|---------------|-------------------|-------------|
| Vulkan (iGPU) | ~314 | ~20 | ~4096 tokens |
| CPU (`-ngl 0`) | ~144 | ~14.3 | 8192+ tokens |

Expect 10-15 seconds per delegation round-trip. The orchestrator is designed around this latency.

## Setup

### 1. Install and start llama.cpp

```bash
# Build llama.cpp (see https://github.com/ggml-org/llama.cpp)
cmake -B build -DGGML_VULKAN=ON  # or -DGGML_CPU_ONLY=ON for CPU-only
cmake --build build --config Release

# Download a quantized model (example: Gemma 4 27B)
# Place it somewhere accessible, e.g. ~/models/

# Start the server
./build/bin/llama-server \
    -m ~/models/gemma-4-27b-it-Q4_K_M.gguf \
    --port 8080 \
    -ngl 99 \          # GPU layers (use 0 for CPU-only)
    -c 8192 \          # Context window
    --temp 0.6
```

### 2. Configure Curunir

```bash
cp .env.example .env
```

Set these in `.env`:

```bash
# Point at your local llama.cpp server
MODEL=openai/gemma-4-27b-it
API_BASE=http://localhost:8080/v1

# Enable orchestrator mode
ORCHESTRATOR_MODE=true

# Match your llama.cpp context window (chars ~ tokens * 4)
MAX_HISTORY_CHARS=16000
```

### 3. Customize your identity

Edit `context/identity.md`. Keep it short — every token counts. One or two lines is ideal:

```markdown
# Hal

You are Hal, a personal assistant.
```

### 4. Start Curunir

```bash
python run.py
```

In another terminal:

```bash
python cli.py --host localhost
```

You should see:

```
Curunir (local mode)
Tip: I work best with focused requests. Ask me to do something specific.

> _
```

## How It Works

### Orchestrator Architecture

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

### Context Budget

In an 8k token window:

| Component | Tokens |
|-----------|--------|
| Orchestrator system prompt | ~300 |
| `delegate` tool schema | ~100 |
| Conversation history | ~7,600 |

Sub-agents get ~7,100-7,600 tokens for their work (system prompt + tool schemas + task + tool results).

### Summary Compaction

After each delegation, the orchestrator compacts the raw tool call exchange into a one-line summary:

```
Before: [assistant tool_call] + [tool result: 500 chars] = ~600 chars
After:  [summary] [system] uptime: 5 days = ~30 chars
```

This keeps the orchestrator's history small so conversations can last longer within the tight context window.

### What's Disabled

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

## CLI Features

### Context Usage Indicator

The prompt shows a 5-block bar indicating how much context has been used:

```
[ctx: ██░░░] > check disk usage
[ctx: ████░] > now check memory
```

At 4/5 blocks the bar turns yellow — time to `/clear` or wrap up.

### Delegation Progress

Tool calls show which sub-agent is working:

```
  ├─ Delegate [system]: run df -h and report free space
```

### Topic Reset

```
> /clear
```

Wipes orchestrator history. Persistent memory (files in `context/memory/`) survives.

## Troubleshooting

**"Sub-agent timed out"** — The model is too slow or the task is too complex. Try simplifying the request, or increase the timeout by setting `_TIMEOUT` in `src/tools/delegate.py`.

**Context overflow errors** — Lower `MAX_HISTORY_CHARS` in `.env`. For a 4k context window (Vulkan), try `MAX_HISTORY_CHARS=8000`.

**Model generates garbage tool calls** — Small quantized models sometimes hallucinate invalid JSON or wrong agent names. The `agent` enum constraint helps, but if it persists, try a larger quantization (Q5_K_M, Q6_K) or a different model.

**Orchestrator delegates when it shouldn't** — The orchestrator prompt says "respond directly" for simple questions. If it over-delegates, you can edit the Rules section in `build_orchestrator_prompt()` in `src/agent/system_prompt.py`.
