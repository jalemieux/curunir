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

Defined in `src/channels/base.py`, shared by all channels. These extend the parent spec's message types with two optional fields:

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass
class IncomingMessage:
    content: str
    channel: str            # "cli", "slack", "email"
    session_id: str         # "cli" for CLI channel
    reply_address: dict     # {} for CLI (stdout is implicit)
    command: str | None = None  # "clear", etc. — extension over parent spec

@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None  # extension over parent spec
```

- `command` field on `IncomingMessage` allows channels to send control signals (e.g., `"clear"` for session reset) without magic strings in `content`.
- `tool_calls` field on `OutgoingMessage` carries tool call summaries for optional verbose rendering.
- Both fields are `None` by default and backward-compatible with the parent spec's definitions.

## Channel Protocol

Defined in `src/channels/base.py`. All channels implement this interface:

```python
class Channel(Protocol):
    async def start(self) -> None:
        """Run the channel's input loop."""
        ...

    async def send(self, msg: OutgoingMessage) -> None:
        """Receive an outbound message for delivery."""
        ...
```

The router depends on `send()`. The startup code calls `start()`. Using a `Protocol` (structural typing) rather than an ABC — channels don't need to inherit, just implement the methods.

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
async def route_outbound(out_queue: asyncio.Queue, channels: dict[str, Channel]):
    while True:
        msg = await out_queue.get()
        channel = channels.get(msg.channel)
        if channel is None:
            logger.warning("No channel registered for %r, discarding message", msg.channel)
            continue
        await channel.send(msg)
```

Channels register themselves in a `dict[str, Channel]` keyed by channel name. The router is the sole consumer of the outbound queue. Unroutable messages are logged and discarded.

## CLIChannel

`src/channels/cli.py` — single class, Rich for terminal UI.

### Constructor

```python
SESSION_ID = "cli"

class CLIChannel:
    def __init__(self, in_queue: asyncio.Queue, console: Console | None = None):
        self.in_queue = in_queue
        self.verbose = False
        self._console = console or Console()
        self._live = None
```

Takes the shared inbound queue and an optional Rich `Console` (injectable for testing). `SESSION_ID` is a module-level constant — CLI always uses a single session.

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
    self._stop_spinner()  # stop any existing spinner first
    self._live = self._console.status("[bold cyan]Thinking...[/bold cyan]")
    self._live.start()

def _stop_spinner(self):
    if self._live:
        self._live.stop()
        self._live = None
```

Rich's `Console.status()` provides an animated spinner. Started when user sends a message, stopped when `send()` is called.

**Concurrency note:** `_start_spinner()` is called from the input loop (which runs stdin reads in a thread executor), and `_stop_spinner()` is called from `send()` (invoked by the router coroutine). Both run on the same asyncio event loop thread — the executor only blocks the `input()` call, not the coroutine. Since asyncio is single-threaded and these methods are called from coroutines (not from within the executor), there are no thread-safety concerns. If the user sends multiple messages before a response arrives, `_start_spinner()` cleans up the previous spinner before starting a new one.

## Dependencies

- `rich` — Console, Markdown, Panel, status spinner
- `asyncio` — Queue, event loop, run_in_executor
- Standard library `dataclasses`

## What This Design Excludes

- Agent loop integration (session management, command handling)
- Other channels (Slack, Email)
- Configuration loading
- Testing strategy (will be defined in the implementation plan)
