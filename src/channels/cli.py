import asyncio

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from src.channels.base import IncomingMessage, OutgoingMessage

SESSION_ID = "cli"


class CLIChannel:
    def __init__(self, in_queue: asyncio.Queue, model: str = "", console: Console | None = None):
        self.in_queue = in_queue
        self.model = model
        self.verbose = False
        self._console = console or Console()
        self._live = None
        self._ready = asyncio.Event()
        self._ready.set()

    async def start(self) -> None:
        await self._input_loop()

    async def send(self, msg: OutgoingMessage) -> None:
        self._stop_spinner()

        if msg.tool_calls and self.verbose:
            for tc in msg.tool_calls:
                line = Text()
                line.append("  ● ", style="bold cyan")
                line.append(tc, style="dim")
                self._console.print(line)

        if msg.content:
            self._console.print(Markdown(msg.content))

        if msg.final:
            self._ready.set()

    async def _input_loop(self) -> None:
        loop = asyncio.get_event_loop()
        if self.model:
            self._console.print(f"[bold]{self.model}[/bold]")
        self._console.print("[dim]type /clear to reset, /verbose to toggle tool output[/dim]\n")

        while True:
            await self._ready.wait()
            try:
                line = await loop.run_in_executor(None, lambda: self._console.input("[bold green]> [/bold green]"))
            except EOFError:
                break

            text = line.strip()
            if not text:
                continue

            if text == "/clear":
                msg = IncomingMessage(content="", channel="cli", session_id=SESSION_ID, reply_address={}, command="clear")
                await self.in_queue.put(msg)
                self._console.print("[dim]Session cleared.[/dim]")
                continue

            if text == "/verbose":
                self.verbose = not self.verbose
                state = "on" if self.verbose else "off"
                self._console.print(f"[dim]Verbose mode {state}.[/dim]")
                continue

            msg = IncomingMessage(content=text, channel="cli", session_id=SESSION_ID, reply_address={})
            await self.in_queue.put(msg)
            self._ready.clear()
            self._start_spinner()

    def _start_spinner(self) -> None:
        self._stop_spinner()
        self._live = self._console.status("[bold cyan]Thinking...[/bold cyan]")
        self._live.start()

    def _stop_spinner(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None
