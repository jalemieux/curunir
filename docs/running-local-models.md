# Running Curunir with Local Models

This guide walks through running Curunir against a locally hosted LLM (llama.cpp, optionally fronted by llama-swap). For the design rationale behind orchestrator mode, see [Orchestrator Architecture](orchestrator-architecture.md).

## Hardware Requirements

Reference hardware:

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

## Setup with llama.cpp

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
```

Curunir reads `n_ctx` from llama.cpp's `/slots` at startup; no manual sizing required.

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

## Setup with llama-swap

[llama-swap](https://github.com/mostlygeek/llama-swap) is a proxy that fronts multiple llama.cpp instances and routes requests to the right one based on the `model` field in the OpenAI-compatible request. Use it when you want to switch between local models without restarting Curunir.

### 1. Install llama-swap

See https://github.com/mostlygeek/llama-swap for installation. In short:

```bash
go install github.com/mostlygeek/llama-swap@latest
```

### 2. Write `config.yaml`

Minimal example with two models:

```yaml
models:
  gemma-27b:
    cmd: >
      ~/llama.cpp/build/bin/llama-server
      -m ~/models/gemma-4-27b-it-Q4_K_M.gguf
      --port 8100 -ngl 99 -c 8192 --temp 0.6
    proxy: http://localhost:8100

  qwen-7b:
    cmd: >
      ~/llama.cpp/build/bin/llama-server
      -m ~/models/qwen-7b-instruct-Q5_K_M.gguf
      --port 8101 -ngl 99 -c 32768 --temp 0.6
    proxy: http://localhost:8101
```

### 3. Start llama-swap

```bash
llama-swap --config config.yaml --listen :8080
```

### 4. Point Curunir at llama-swap

In `.env`:

```bash
API_BASE=http://localhost:8080/v1
MODEL=openai/gemma-27b     # must match a name in config.yaml
ORCHESTRATOR_MODE=true
```

### Known limitation

Curunir reads `n_ctx` **once at startup** from whichever llama.cpp instance llama-swap activates first. If you switch to a model with a different `n_ctx` mid-session (by changing `MODEL` and hitting llama-swap again), the budget will be stale. **Restart Curunir after changing the active model.** Automating live re-resolution is out of scope for now.

## CLI Features

### Context Usage Indicator

The prompt shows a 5-block bar indicating how much of the context window is in use, computed from the last call's real `prompt_tokens` divided by `n_ctx`:

```
[ctx: ██░░░] > check disk usage
[ctx: ████░] > now check memory
```

At 4/5 blocks the bar turns yellow — time to `/clear` or wrap up. The bar appears only when `API_BASE` is set (llama.cpp). For hosted models the bar is hidden because the real window is unknown.

After each turn the stats line reports live KV usage as a percentage:

```
ctx: 4821 tok (14% of 8192) | 762 completion tok | 9.2 tok/s | 3 steps | 130.9s
```

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

**Sub-agent timed out** — The model is too slow or the task is too complex. Try simplifying the request, or increase the timeout by setting `_TIMEOUT` in `src/tools/delegate.py`.

**llama.cpp unreachable at startup** — Curunir fails fast when `/slots` can't be reached. Check that `llama-server` (or `llama-swap`) is running and listening at the `API_BASE` URL.

**Model generates garbage tool calls** — Small quantized models sometimes hallucinate invalid JSON or wrong agent names. The `agent` enum constraint helps, but if it persists, try a larger quantization (Q5_K_M, Q6_K) or a different model.

**Orchestrator delegates when it shouldn't** — The orchestrator prompt says "respond directly" for simple questions. If it over-delegates, you can edit the Rules section in `build_orchestrator_prompt()` in `src/agent/system_prompt.py`.
