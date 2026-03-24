# Tools, Skills, and Workflows — A Consolidated Framework

## The Three Primitives

### Tools

Atomic, callable functions. Defined inputs, outputs, and side effects. They do one thing — send an email, fetch a price, run a bash command, query an API. The model doesn't need guidance to use a tool; it needs to know the tool exists and what it accepts. Tools are stateless and context-free. They don't know *why* they're being called.

### Skills

Guided use of tools to achieve an outcome. A skill encodes judgment — which tools to use, in what order, with what constraints, and how to interpret the results. The key characteristic: **the model is making decisions at runtime**. It's interpreting intent, choosing between tools, adapting to what comes back, and shaping the output. Skills are loaded lazily into the prompt as instructions that help the model reason better within a domain.

A skill answers the question: "how should I approach this kind of problem?"

**Litmus test:** does the model need to interpret intent and decide which tools to use and how? That's a skill.

*Example:* "What's happening with gold right now?" — the model needs to decide whether that means price action, geopolitical drivers, or technical levels. It picks the right APIs, decides how deep to go, and shapes the response. The skill guides that judgment.

### Workflows

Deterministic and scripted. The steps, tools, and order are known at design time. No model judgment is needed for routing or tool selection — the pipeline is fixed. An LLM might be used *inside* a workflow step for generation or synthesis, but it's not *driving* the flow. Your application code is.

**Litmus test:** are the steps and tools already known before runtime? That's a workflow. If you can draw the flowchart before it ever executes, it's a workflow.

*Example:* "Morning market briefing" — fetch market data, fetch news, call LLM to synthesize, deliver via email. Same steps every time. The LLM is a text processor at one step, not a decision-maker across steps.

## Where the Lines Sit

**Tools → Skills:** the boundary is *judgment*. When you go from "call this function" to "figure out which functions to call and how to use them together," you've crossed into a skill.

**Skills → Workflows:** the boundary is *determinism*. When the path through the tools is fixed and doesn't require the model to decide what to do next, you've crossed into a workflow. Skills require runtime reasoning. Workflows don't.

## The JIT Compiler Pattern

Bridges skills and workflows: the agent initially schedules recurring tasks as prompted crons (skill-driven, expensive). In the background, the runtime captures the tool call trace from a real execution and compiles it into a deterministic script, then silently swaps the cron entry. Skills get "promoted" into workflows through observation, not upfront design. The user and the agent never need to know the difference.
