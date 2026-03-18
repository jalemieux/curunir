# Agentic Loop Architecture — Design Notes

## Problem Statement

Running the agent on Sonnet 4.6 consumes ~$3–5/day. Not sustainable for a personal assistant.

## Context

Two architectural options to bring token costs down without sacrificing too much agency.

---

## Option 1: Two-Model Orchestrator + Worker Pattern

### How it works

A smart, expensive **orchestrator model** (Opus 4.6, GPT-5.x) handles planning, goal decomposition, and decision-making. It calls what it thinks are standard tools (bash, file system, etc.), but those tools are actually **wrapped around a smaller, cheaper worker model** (Haiku 4.5, Gemini Flash).

The worker's job is not blind execution — it acts as an **intelligent execution adapter**:

- Validates the command makes sense in the local environment
- Handles retries and partial failures cheaply
- Normalizes output back to the orchestrator in a structured format
- Fails loudly and clearly rather than silently half-succeeding

### The key insight

The orchestrator stays **environment-agnostic** — it thinks in intentions, not OS paths or shell quirks. The worker abstracts all of that. Opus burns zero extra tokens on execution details; retries and grounding happen at the Haiku layer cheaply.

### Trade-offs

| Pro | Con |
|---|---|
| Opus token budget stays lean | Pure cost optimization — no added agency |
| Failures contained at worker layer | Two system prompts to maintain |
| Worker handles env-specific grounding | Debugging gets harder (failure could be at either layer) |
| Retry loops don't drain orchestrator context | Inter-model communication contract adds complexity |

### When it makes sense

Only worth the complexity if Opus token consumption is the actual bottleneck — high-volume workloads, long sessions, or continuous loops. For occasional personal assistant use, the architecture tax likely outweighs the savings.

---

## Option 2: Replace Expensive Model with Open-Source / Cheaper Frontier Model

### How it works

Keep the single-model architecture (simpler, easier to debug), but swap Opus 4.6 for a cheaper model that is still capable enough for the orchestration role. Candidates:

- **Kimi K2** — strong on long-horizon agentic coding tasks, 256k context, improving fast
- **MiniMax M2.5** — MoE architecture, low cost, solid tool use and coding performance
- **Gemini 2.5 Flash** (thinking mode) — adjustable reasoning depth, cheapest at scale, massive context
- **DeepSeek V3.2** — open weights, GPT-5 class reasoning claims, strong agentic tool use

### Trade-offs

| Pro | Con |
|---|---|
| Single model = simpler architecture | Less reliable instruction fidelity than Opus |
| Much cheaper per token | May require more hand-holding in system prompt |
| Self-hostable options available (DeepSeek) | Less safety/alignment maturity |
| Good enough for most personal assistant tasks | Performance varies a lot by task type |

### When it makes sense

Best fit when the bottleneck is **cost, not capability**. For personal assistant workloads — task execution, file ops, research, coding help — a capable mid-tier model is often indistinguishable from Opus in practice. The ceiling only matters on genuinely hard reasoning tasks.

---

## Recommendation

Start with **Option 2** — swap the model, keep the architecture simple. Test Kimi K2 or Gemini 2.5 Flash (thinking) across your actual looper workload. Only revisit Option 1 if you find a specific task class where a cheaper orchestrator fails and you need Opus-level planning but still want to contain costs on the execution side.

The two-model pattern makes more sense at scale (platform/enterprise workloads) than for a personal assistant running on a MacBook.

---

## Addendum: OpenRouter Testing (2026-03-18)

Added support for configurable model, API base, and OpenRouter provider routing via env vars (`MODEL`, `API_BASE`, `OPENROUTER_PROVIDER`). This lets us point the agent at any model available through OpenRouter and pin a specific inference provider.

### Kimi K2.5

Tried Kimi K2.5 via OpenRouter (DeepInfra provider). The model was unable to handle tool call responses — it would make tool calls, receive results, and then repeat the same calls (e.g. reading the same file 3 times in a single turn). It couldn't track what it had already done from the conversation history. Not viable for an agentic loop that relies on coherent multi-step tool use.

### GLM-5-Turbo

Settled on GLM-5-Turbo (via OpenRouter) for now. Handles tool calling correctly and is significantly cheaper than Sonnet 4.6.
