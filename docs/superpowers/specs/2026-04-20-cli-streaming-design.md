# CLI Streaming — Design

**Issue:** [#24 — Add streaming responses to CLI](https://github.com/jalemieux/curunir/issues/24)

**Date:** 2026-04-20

## Goal

Stream assistant text token-by-token from the LLM through the WebSocket channel to the CLI, so users see progress incrementally instead of waiting for the full agent turn to complete. Default-on, no toggle. Email channel unchanged.

## Decisions

- **Stream all assistant text**, both intermediate preambles ("I'll check X first…") and the final answer.
- **Plain-text live append**, then re-render as Markdown when the LLM iteration ends. No live Markdown re-rendering (avoids flicker).
- **Default-on, no flag.** Streaming is strictly an upgrade for interactive use.
- **Callback-based plumbing.** `call_llm` gains an optional `on_text_delta` callback; return type is unchanged.

## Wire Protocol

`OutgoingMessage` gets one new field:

```python
@dataclass
class OutgoingMessage:
    ...
    delta: bool = False
```

WS payload gains a matching `delta` key. Three message shapes:

| Shape | Meaning |
|---|---|
| `{content: "<chunk>", delta: true, final: false}` | Append `<chunk>` to current streaming buffer. No other fields meaningful. |
| `{tool_calls: [...], final: false}` | Flush streaming buffer (commit as Markdown), then show tool call. |
| `{content: "<full text>", final: true, stats: {...}}` | Flush remaining buffer; render stats. `content` is the agent's final answer (used to avoid double-printing — see CLI section). |

Email channel ignores `delta` — `EmailChannel.send` is invoked only on `final: true`, same as today.

## `src/llm.py`

`call_llm` gains an optional parameter:

```python
async def call_llm(
    model: str,
    messages: list[dict],
    tools: list[dict],
    api_base: str | None = None,
    openrouter_provider: str | None = None,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResponse:
```

When `on_text_delta` is `None`: behavior is identical to today (single non-streaming `acompletion` call). Email path is untouched.

When `on_text_delta` is provided:

1. Call `litellm.acompletion(stream=True, stream_options={"include_usage": True}, **kwargs)`.
2. `async for chunk in response`:
   - `chunk.choices[0].delta.content` → `await on_text_delta(text)`; append to text accumulator.
   - `chunk.choices[0].delta.tool_calls` → merge by `index`: concatenate `function.arguments`, capture `function.name` and `id` on first sight.
   - `chunk.usage` (only on terminal chunk when `include_usage=True`) → populate `LLMUsage`.
3. Return `LLMResponse(text, tool_calls, usage)` — same shape callers expect today.

The retry loop (429/502/503) wraps the streaming call too. If a provider doesn't emit a final usage chunk, usage fields stay zero — downstream stats handling already tolerates this (see `agent.py` finalize_stats).

## `src/agent/agent.py`

`Agent.handle()` gains an `on_text_delta` parameter, threaded through to every `call_llm` invocation in the loop (both the primary call and the post-context-overflow retry call).

```python
async def handle(
    self, message: str | list, session_id: str,
    on_tool_call=None, attachments=None,
    system_task_prompt=None, metadata=None,
    stop_event=None,
    on_text_delta=None,
) -> str:
```

No other changes to the loop. Each LLM iteration that produces text streams it out before moving on (either to a tool call or to the final return).

## `run.py` `agent_worker`

Add a delta callback alongside the existing `on_tool_call`:

```python
async def on_text_delta(chunk: str):
    await out_queue.put(OutgoingMessage(
        content=chunk,
        channel=msg.channel,
        session_id=msg.session_id,
        reply_address=msg.reply_address,
        delta=True,
        final=False,
    ))
```

Pass it to `agent.handle(..., on_text_delta=on_text_delta)`. The final `OutgoingMessage` (with stats, attachments, full text) is emitted as today.

## `src/channels/ws.py`

Include `delta` in the JSON payload:

```python
payload: dict = {
    "content": msg.content,
    "tool_calls": msg.tool_calls,
    "final": msg.final,
    "delta": msg.delta,
    "attachments": ...,
    "workflow": msg.workflow,
    "stats": msg.stats,
}
```

No other changes.

## `cli.py`

Add a stream-region state machine to `output_loop`:

```python
buffer = ""
live: Live | None = None

async for raw in ws:
    data = json.loads(raw)

    # Handle delta messages
    if data.get("delta"):
        if live is None:
            stop_spinner()
            buffer = ""
            live = Live(Text(""), console=console, transient=True, refresh_per_second=20)
            live.start()
        chunk = data.get("content") or ""
        buffer += chunk
        live.update(Text(buffer))
        continue

    # Non-delta: flush any active stream first
    flushed_text = ""
    if live is not None:
        live.stop()  # transient=True erases the live region
        live = None
        flushed_text = buffer
        buffer = ""
        if flushed_text.strip():
            console.print(Markdown(flushed_text))

    # Then handle the message exactly as today, with one tweak:
    # Skip the "if content: console.print(Markdown(content))" block when
    # `flushed_text` already covered this final answer (i.e. content == flushed_text).
    ...
```

**Avoiding double-print:** the final message contains the full final answer in `content`. To prevent rendering it twice when streaming covered it, the rule is: **if `flushed_text` is non-empty at the point we're about to handle the message's `content`, skip that `console.print(Markdown(content))` call.** Tool-iteration messages don't trigger this since their `content` is empty by today's convention; only the `final: true` message carries `content`.

**Spinner:** stopped when first delta arrives. The existing `start_spinner()` at submit time still covers the gap before the first token.

## Testing

- `tests/test_llm.py` (new or extend): mock `litellm.acompletion` to return an async iterator of chunks; assert `on_text_delta` fires per chunk and `LLMResponse` aggregates correctly (text, tool_calls merged by index, usage from terminal chunk).
- `tests/test_agent.py`: assert `on_text_delta` is forwarded to `call_llm` on every iteration.
- `tests/test_channels.py`: assert WS payload includes `delta` field; assert `EmailChannel.send` is never invoked with `delta=True` (it's only sent on `final: true`).
- No CLI integration test — `cli.py` is uncovered by tests today and a Live/Rich integration test would be high-effort, low-value. The render path is small enough to verify by hand.

## Out of Scope

- Streaming for the email channel.
- Streaming tool-call arguments to the user (the partial JSON is internal-only; tool calls surface to the CLI as the existing `tool_calls` notification once fully accumulated).
- Live Markdown re-rendering during the stream (rejected for flicker).
- Toggle / opt-out env var.

## Open Questions Resolved

- *"Should streaming be opt-in?"* → No, default-on, no flag.
- *"How should partial tool-call arguments be surfaced?"* → Not surfaced. Accumulate internally; emit the existing `tool_calls` notification when complete.
