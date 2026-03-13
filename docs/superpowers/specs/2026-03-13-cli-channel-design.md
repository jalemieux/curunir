# CLI Channel — Design Spec

## Overview

The CLI channel is the terminal-based interface for the Valar agent framework. It reads user input from stdin, pushes messages to a shared inbound queue, and renders agent responses to stdout via Rich when called by the outbound router.

## Scope

This spec covers:
- `src/channels/base.py` — shared message types
- `src/channels/cli.py` — CLI channel implementation
- `src/channels/router.py` — outbound message router

This spec does NOT cover the agent loop, tools, LLM integration, skills, or memory.

## Message Types

Defined in `src/channels/base.py`, shared by all channels:

```python
from dataclasses import dataclass

@dataclass
class IncomingMessage:
    content: str
    channel: str            # "cli", "slack", "email"
    session_id: str         # "cli" for CLI channel
    reply_address: dict     # {} for CLI (stdout is implicit)
    command: str | None = None  # "clear", etc.

@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None
```

- `command` field on `IncomingMessage` allows channels to send control signals (e.g., `"clear"` for session reset) without magic strings in `content`.
- `tool_calls` field on `OutgoingMessage` carries tool call summaries for optional verbose rendering.

## Architecture

```
InQueue (shared asyncio.Queue)     OutQueue (shared asyncio.Queue)
  ▲                                   │
  │                                   ▼
CLI.start() ──push──►           route_outbound()
Slack ──push──►                   │  │  │
Email ──push──►                  ▼  ▼  ▼
                            cli.send()
                            slack.send()
                            email.send()
```

- One shared `InQueue`, one shared `OutQueue` — both `asyncio.Queue` instances.
- All channels push to `InQueue`. Only the router pulls from `OutQueue`.
- The router dispatches to the correct channel's `send()` method based on `msg.channel`.
- Channels never read from `OutQueue` directly.

## Router

`src/channels/router.py` — a single async function:

```python
async def route_outbound(out_queue: asyncio.Queue, channels: dict[str, object]):
    while True:
        msg = await out_queue.get()
        channel = channels[msg.channel]
        await channel.send(msg)
```

Channels register themselves in a `dict[str, Channel]` keyed by channel name. The router is the sole consumer of the outbound queue.

## CLIChannel

`src/channels/cli.py` — single class, Rich for terminal UI.

### Constructor

```python
class CLIChannel:
    def __init__(self, in_queue: asyncio.Queue, console: Console | None = None):
        self.in_queue = in_queue
        self.verbose = False
        self._console = console or Console()
        self._live = None
```

Takes the shared inbound queue and an optional Rich `Console` (injectable for testing).

### Input Loop

`start()` runs the input loop as the channel's main task:

```python
async def start(self):
    await self._input_loop()

async def _input_loop(self):
    loop = asyncio.get_event_loop()
    self._console.print("[bold]Valar CLI[/bold] — type /clear to reset, /verbose to toggle tool output\n")

    while True:
        try:
            line = await loop.run_in_executor(None, lambda: self._console.input("[bold green]> [/bold green]"))
        except EOFError:
            break

        text = line.strip()
        if not text:
            continue

        if text == "/clear":
            msg = IncomingMessage(content="", channel="cli", session_id="cli", reply_address={}, command="clear")
            await self.in_queue.put(msg)
            self._console.print("[dim]Session cleared.[/dim]")
            continue

        if text == "/verbose":
            self.verbose = not self.verbose
            state = "on" if self.verbose else "off"
            self._console.print(f"[dim]Verbose mode {state}.[/dim]")
            continue

        msg = IncomingMessage(content=text, channel="cli", session_id="cli", reply_address={})
        await self.in_queue.put(msg)
        self._start_spinner()
```

- Uses `run_in_executor` to read stdin without blocking the event loop.
- `Console.input()` for styled prompt.
- `/clear` sends a command message to the queue so the agent loop can reset session state.
- `/verbose` toggles local state only — no queue message needed.
- Spinner starts after pushing a user message.

### Output (send method)

```python
async def send(self, msg: OutgoingMessage):
    self._stop_spinner()

    if msg.tool_calls and self.verbose:
        for tc in msg.tool_calls:
            self._console.print(Panel(tc, title="Tool Call", border_style="dim"))

    self._console.print(Markdown(msg.content))
```

Called by the router. Stops the spinner, optionally renders tool call panels, then renders the response as markdown.

### Spinner

```python
def _start_spinner(self):
    self._live = self._console.status("[bold cyan]Thinking...[/bold cyan]")
    self._live.start()

def _stop_spinner(self):
    if self._live:
        self._live.stop()
        self._live = None
```

Rich's `Console.status()` provides an animated spinner. Started when user sends a message, stopped when `send()` is called.

## Dependencies

- `rich` — Console, Markdown, Panel, status spinner
- `asyncio` — Queue, event loop, run_in_executor
- Standard library `dataclasses`

## What This Design Excludes

- Agent loop integration (session management, command handling)
- Other channels (Slack, Email)
- Configuration loading
- Testing strategy (will be defined in the implementation plan)
