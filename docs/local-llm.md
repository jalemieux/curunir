# Running a local LLM backend

Curunir talks to any OpenAI-compatible server through LiteLLM, so you can
point it at a local model server (llama.cpp / `llama-server`, Ollama, vLLM,
LM Studio) instead of a hosted API. This doc covers the curunir-side wiring
and the host-side tuning that actually matters on Apple Silicon, plus the
commands used to diagnose a misbehaving setup.

The running example is **llama.cpp `llama-server`** hosting
`unsloth/Qwen3.6-35B-A3B-GGUF` (a MoE: 35B total params, ~3B active per
token) on a 48 GB Apple Silicon Mac, with curunir running in Docker.

## Building `llama-server` (Apple Silicon)

There are no custom build flags to set. On macOS, llama.cpp's defaults
already do the right thing — the plain build is the correct build:

```bash
cd ~/Dev/models/llama.cpp   # wherever your clone lives
cmake -B build
cmake --build build --config Release -j
# binaries land in build/bin/ — llama-server, llama-bench, etc.
```

**Why no flags.** The defaults enable Metal, link Accelerate (BLAS), and
detect CPU features automatically. On Apple Silicon the configure step finds
`dotprod + i8mm + sme` and auto-compiles with
`-mcpu=native+dotprod+i8mm+nosve+sme`. The **SME path** (Scalable Matrix
Extension, exposed on M4/M5-class chips) matters: llama.cpp ships SME-tuned
CPU kernels that get used automatically when the chip exposes it. Passing
`GGML_METAL`/BLAS/`-march` flags by hand is redundant at best and can
*disable* this auto-detection — leave them off.

**Two benign configure-time warnings**, neither of which affects runtime
throughput:

| Warning | Meaning |
| ------- | ------- |
| `OpenMP not found` | irrelevant on this stack — Accelerate and Metal carry the compute |
| `ccache not found` | only a rebuild-speedup tool; install it (`brew install ccache`) if you rebuild often |

**Updating + rebuilding.** A rebuild takes ~2–5 min on this machine, so only
rebuild when the source actually changed:

```bash
cd ~/Dev/models/llama.cpp
git fetch origin
git pull --ff-only origin master      # fast-forward only
cmake -B build                        # re-run only if sources changed
cmake --build build --config Release -j
git rev-parse --short=9 HEAD          # record the commit you built
```

If the repo is already at the latest commit and `build/bin/llama-server`
exists, the build is a no-op — skip it. **Never auto-stash:** if the working
tree is dirty, stop and resolve it by hand rather than stashing, or you risk
losing local work.

## Pointing curunir at the model

Two `.env` keys:

```bash
MODEL=openai/Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf   # openai/ prefix → LiteLLM uses the OpenAI-compatible path
API_BASE=http://host.docker.internal:8080/v1   # in Docker, reach the host this way (NOT localhost)
```

- The string after `openai/` must match the model **id** the server
  advertises. Check it with `curl -s http://localhost:8080/v1/models`.
- `OPENAI_API_KEY` still has to be set (any non-empty value) — LiteLLM's
  OpenAI path requires it even though `llama-server` ignores it.

### `localhost` vs `host.docker.internal` (Docker gotcha)

Inside a container, `localhost`/`127.0.0.1` means *the container*, not your
Mac. To reach a server running on the host:

| curunir runs… | `API_BASE` host |
| ------------- | --------------- |
| on the host (`python run.py`) | `localhost` |
| in Docker, model on the host  | `host.docker.internal` |
| all-in-Docker (model is another compose service) | the service name |

On **Docker Desktop for Mac**, `host.docker.internal` is proxied through the
Desktop VM and reaches host services even when they bind only to
`127.0.0.1` — so you do *not* need to rebind the model server to `0.0.0.0`.
On **Linux**, `host.docker.internal` does not resolve by default; add this
to the curunir service in compose:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### Stale-env gotcha

`env_file` is read **once, at container start**. If you edit `.env` while
the container is up, the running process keeps the old values. After any
`.env` change: `docker compose up -d` (recreates) or
`docker compose restart curunir`. Confirm what the live process actually
has:

```bash
docker exec curunir-curunir-1 sh -c 'echo "MODEL=$MODEL  API_BASE=$API_BASE"'
```

## Apple Silicon tuning (`llama-server`)

Three flags dominate behavior. Getting them wrong is the difference between
CPU-bound at ~5 tok/s and GPU-accelerated at ~47 tok/s.

### `-ngl` — GPU offload (the big one)

`-ngl N` offloads N layers to the Metal GPU. **`-ngl 0` means CPU-only** —
the GPU sits idle while CPU cores peg at ~98% and load average climbs.
Almost always you want **`-ngl 99`** (offload everything). On Apple Silicon
memory is unified, so GPU offload barely changes RAM use but moves the
matmuls off the CPU.

Symptom of `-ngl 0`: several CPU cores at ~98%, high load average,
`llama-server` in the `R` (running) state at ~99% CPU. After `-ngl 99`:
cores idle, `llama-server` in `S` (sleep) at 0% CPU between requests,
compute on the GPU.

### `-c` — context size (the memory lever)

`-c N` sets the context window in tokens, and the KV cache for it is
allocated up front — this is usually the largest single chunk of memory the
server holds. `-c 262144` (256k) on a 48 GB machine pushed it into ~8 GB of
swap. Unless you genuinely send prompts that long, set something realistic:

| `-c` value | KV cache | Use |
| ---------- | -------- | --- |
| `262144` (256k) | very large | only if you really feed 200k+ token prompts |
| `131072` (128k) | large | comfortable headroom on 48 GB |
| `65536` (64k)   | moderate | frees several GB; fine for most agent work |
| `32768` (32k)   | small | tight RAM, or running other things alongside |

Watch for swap, not just the RAM bar: in htop a **red Swap bar means
paging**, which slows inference and wears the SSD. Note that macOS does not
eagerly reclaim swap — once memory pressure is gone, an already-full Swap
bar is *stale* and harmless; it drains lazily or clears on reboot.

### `--reasoning-budget` — cap thinking tokens

Qwen3.6 is a reasoning model (it emits `reasoning_content`). By default
`--reasoning-budget -1` (unrestricted), which shows up in the server log as
`reasoning-budget: activated, budget=2147483647`. With no cap, a hard prompt
can produce a very long chain of thought — and in curunir's agent loop that
cost is paid on *every* tool-calling iteration.

```
--reasoning-budget N    -1 = unrestricted (default)
                         0 = no thinking at all (answer immediately)
                        N>0 = hard cap at N thinking tokens, then finalize
```

The budget counts **thinking tokens only**, not the final answer. Pair it
with `--reasoning-budget-message` for a graceful cutoff (text injected
before the forced end-of-thinking tag so the model wraps up rather than
getting chopped mid-thought). Suggested starting point for agent use:
`1024`, drop toward `512`/`256` if turns still feel long. Both also read
env vars: `LLAMA_ARG_THINK_BUDGET`, `LLAMA_ARG_THINK_BUDGET_MESSAGE`.

### Example launch

```bash
llama-server \
  -ngl 99 -c 65536 \
  -m   /path/to/Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf \
  --mmproj /path/to/mmproj-BF16.gguf \          # vision projector — keep if you use image input
  --reasoning-budget 1024 \
  --reasoning-budget-message "Reasoning budget reached — finalizing the answer now."
```

## Reading the `llama-server` timing log

After each request the server prints a timing block:

```
prompt eval time = 3741 ms / 2753 tokens (735.89 tok/s)   ← prefill (prompt processing)
       eval time = 15467 ms / 723 tokens  (46.75 tok/s)   ← decode (generation)
      total time = 19208 ms / 3476 tokens
```

- **prefill tok/s** — how fast it ingests the prompt. GPU-bound.
- **decode tok/s** — how fast it generates. For an A3B MoE this runs like a
  ~3B model (~47 tok/s) despite the 35B total size.
- A slow *turn* with healthy decode tok/s just means the model generated a
  lot of tokens — that's the model, not the infrastructure.

**Prompt caching / context checkpoints.** Lines like
`created context checkpoint 8 of 32 ... size = 62.813 MiB` mean llama.cpp is
caching the KV prefix and reusing it. You'll see a 23k-token context where
prefill only re-evaluated ~2.7k tokens — the rest was a cache hit. This is
what keeps curunir's growing-history agent loop responsive (the same prefix
is re-sent each iteration). Cost: the checkpoints occupy a couple GB of KV
state, part of the resident footprint.

## Diagnostics cheat sheet

```bash
# What's serving on :8080, and is it loopback-only?
lsof -nP -iTCP:8080 -sTCP:LISTEN

# Model id the server advertises (must match MODEL after the openai/ prefix)
curl -s http://localhost:8080/v1/models | python3 -m json.tool

# End-to-end reachability + a real completion FROM INSIDE the container
docker exec curunir-curunir-1 sh -c \
  'curl -s -m 30 -w "\nHTTP %{http_code}\n" http://host.docker.internal:8080/v1/chat/completions \
   -H "Content-Type: application/json" \
   -d "{\"model\":\"<model-id>\",\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}],\"max_tokens\":10}"'

# What env the LIVE container actually has (catches stale .env)
docker exec curunir-curunir-1 sh -c 'echo "$MODEL | $API_BASE"'

# curunir's own errors (LOG_FILE on the workspace volume)
docker exec curunir-curunir-1 sh -c 'grep -iE "error|refused|connect|litellm" /app/workspace/curunir.log | tail -25'

# Host CPU/GPU/memory/swap pressure
htop                       # cores, Mem + Swap bars, load average
sysctl vm.swapusage        # live swap used/free
```

A subtle one: old connection errors persist in `curunir.log` because the log
lives on a Docker volume that survives restarts. Check the **timestamp** —
an error from three days ago against a container started today is stale, not
a live failure. Truncate with
`docker exec curunir-curunir-1 sh -c ': > /app/workspace/curunir.log'` if old
noise is confusing the picture.
