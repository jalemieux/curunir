"""Standalone WebSocket CLI client for Curunir.

Usage:
    python cli.py [--host localhost] [--port 8765]
"""
import argparse
import asyncio
import json

import websockets
import websockets.exceptions
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

# Reconnection settings
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 30.0


async def _connect_with_retry(uri: str, console: Console) -> websockets.ClientConnection:
    """Connect to *uri*, retrying with exponential backoff until successful."""
    delay = _BACKOFF_BASE
    attempt = 0
    while True:
        try:
            ws = await websockets.connect(uri)
            if attempt > 0:
                console.print(f"[green]Reconnected to {uri}[/green]")
            return ws
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            attempt += 1
            console.print(
                f"[yellow]Cannot connect to {uri} ({exc}). "
                f"Retrying in {delay:.0f}s…[/yellow]"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _BACKOFF_MAX)


async def run(host: str, port: int, console: Console | None = None) -> None:
    console = console or Console()
    uri = f"ws://{host}:{port}"

    console.print(f"[bold]Curunir[/bold] [dim]({uri})[/dim]")
    console.print("[dim]type /clear or /new to reset, /reset to reset without extracting, /verbose to toggle tool output[/dim]")

    verbose = True
    ready = asyncio.Event()

    # Spinner handle
    spinner: object = None  # Rich Live/status object

    def start_spinner() -> None:
        nonlocal spinner
        stop_spinner()
        spinner = console.status("[bold cyan]Thinking…[/bold cyan]")
        spinner.start()  # type: ignore[attr-defined]

    def stop_spinner() -> None:
        nonlocal spinner
        if spinner is not None:
            spinner.stop()  # type: ignore[attr-defined]
            spinner = None

    # ------------------------------------------------------------------ #
    # Output loop: receives messages from the server and renders them.    #
    # ------------------------------------------------------------------ #
    async def output_loop(ws: websockets.ClientConnection) -> None:
        # Streaming state: when the server sends delta messages, we accumulate
        # them in `stream_buffer` and display them in a transient Live region.
        # On the next non-delta message, we close the Live (which erases the
        # plain-text region) and re-print the buffer as Markdown.
        stream_buffer: list[str] = []
        stream_live: Live | None = None

        def flush_stream() -> str:
            """Stop the Live region and return the accumulated text."""
            nonlocal stream_live
            if stream_live is None:
                return ""
            stream_live.stop()
            stream_live = None
            text = "".join(stream_buffer)
            stream_buffer.clear()
            return text

        pending_tool_calls: list[str] = []

        def flush_tool_calls() -> None:
            if not pending_tool_calls:
                return
            console.file.write("\033[A\033[2K")
            line = Text()
            line.append("  \u256f\u2500 ", style="dim")
            line.append(pending_tool_calls[-1])
            console.print(line)
            pending_tool_calls.clear()

        try:
            async for raw in ws:
                data = json.loads(raw)

                stop_spinner()

                # Streaming delta — append to buffer and update Live region
                if data.get("delta"):
                    chunk = data.get("content") or ""
                    if stream_live is None:
                        stream_buffer.clear()
                        stream_live = Live(
                            Text(""),
                            console=console,
                            transient=True,
                            refresh_per_second=20,
                        )
                        stream_live.start()
                    stream_buffer.append(chunk)
                    stream_live.update(Text("".join(stream_buffer)))
                    continue

                # Welcome message with model info
                if "model" in data:
                    console.print(f"[dim]model: {data['model']}[/dim]\n")
                    ready.set()
                    continue

                tool_calls = data.get("tool_calls") or []
                content = data.get("content") or ""
                final = data.get("final", False)
                attachments = data.get("attachments") or []

                # Flush any accumulated stream first; render it as Markdown.
                streamed_text = flush_stream()
                if streamed_text.strip():
                    console.print(Markdown(streamed_text))

                if verbose and tool_calls:
                    for tc in tool_calls:
                        pending_tool_calls.append(tc)
                        line = Text()
                        line.append("  \u251c\u2500 ", style="dim")
                        line.append(tc)
                        console.print(line)

                if content and not streamed_text:
                    if verbose:
                        flush_tool_calls()
                    console.print(Markdown(content))

                if attachments:
                    for att in attachments:
                        line = Text()
                        line.append("  \U0001f4ce ", style="dim")
                        line.append(att.get("filename", ""), style="bold")
                        if "path" in att:
                            line.append(f" \u2192 {att['path']}", style="dim")
                        console.print(line)
                        if att.get("content"):
                            console.print(Markdown(att["content"]))

                # Display stats in verbose mode
                stats = data.get("stats")
                if verbose and stats and final:
                    stat_line = Text()
                    stat_line.append("\n  ", style="dim")
                    parts = []
                    if stats.get("prompt_tokens"):
                        parts.append(f"prompt: {stats['prompt_tokens']} tok")
                    if stats.get("completion_tokens"):
                        parts.append(f"completion: {stats['completion_tokens']} tok")
                    if stats.get("completion_tps"):
                        parts.append(f"{stats['completion_tps']} tok/s")
                    if stats.get("iterations"):
                        parts.append(f"{stats['iterations']} iter")
                    if stats.get("wall_elapsed_sec"):
                        parts.append(f"{stats['wall_elapsed_sec']}s wall")
                    if parts:
                        stat_line.append(" | ".join(parts), style="dim cyan")
                        console.print(stat_line)

                    # llama.cpp server stats
                    server = stats.get("server")
                    if server:
                        for slot in server.get("slots", []):
                            srv_parts = []
                            if slot.get("n_ctx"):
                                srv_parts.append(f"n_ctx: {slot['n_ctx']}")
                            if slot.get("n_past") is not None:
                                srv_parts.append(f"n_past: {slot['n_past']}")
                            if slot.get("prompt_tps"):
                                srv_parts.append(f"prompt: {slot['prompt_tps']} tok/s")
                            if slot.get("generation_tps"):
                                srv_parts.append(f"gen: {slot['generation_tps']} tok/s")
                            if srv_parts:
                                srv_line = Text()
                                srv_line.append("  ", style="dim")
                                srv_line.append(f"slot {slot.get('id', '?')}: ", style="dim")
                                srv_line.append(" | ".join(srv_parts), style="dim yellow")
                                console.print(srv_line)

                if final:
                    if verbose:
                        flush_tool_calls()
                    ready.set()
        finally:
            # Stop any in-flight Live region so the terminal isn't left
            # in a partial render state if the connection drops.
            if stream_live is not None:
                stream_live.stop()
                stream_live = None
                stream_buffer.clear()
            # Unblock the input loop if the connection dropped before final:true
            ready.set()

    # ------------------------------------------------------------------ #
    # Main loop: manages connection lifetime and the input loop.          #
    # ------------------------------------------------------------------ #
    loop = asyncio.get_running_loop()
    ws = await _connect_with_retry(uri, console)

    # A payload that failed to send (due to connection drop) and should be
    # retried on the next connection.
    pending_payload: dict | None = None

    while True:
        # Launch output reader for the current connection
        out_task = asyncio.create_task(output_loop(ws))

        # Input loop
        try:
            while True:
                # If a payload failed to send on a previous connection, retry it
                # now rather than reading new input.
                if pending_payload is not None:
                    payload = pending_payload
                    pending_payload = None
                else:
                    await ready.wait()

                    # Read from stdin without blocking the event loop
                    try:
                        line = await loop.run_in_executor(
                            None,
                            lambda: console.input("[bold green]> [/bold green]"),
                        )
                    except EOFError:
                        # Ctrl-D: close cleanly and exit
                        out_task.cancel()
                        await ws.close()
                        return

                    text = line.strip()
                    if not text:
                        continue

                    if text == "/verbose":
                        verbose = not verbose
                        state = "on" if verbose else "off"
                        console.print(f"[dim]Verbose mode {state}.[/dim]")
                        continue

                    if text in ("/clear", "/new"):
                        payload = {"content": "", "command": "clear"}
                    elif text == "/reset":
                        payload = {"content": "", "command": "reset"}
                    else:
                        payload = {"content": text, "command": None}

                try:
                    await ws.send(json.dumps(payload))
                except websockets.exceptions.ConnectionClosed:
                    # Save the payload so it can be retried after reconnection
                    pending_payload = payload
                    break

                ready.clear()
                start_spinner()

        except KeyboardInterrupt:
            out_task.cancel()
            await ws.close()
            return

        # If we reach here, the connection dropped while in the input loop or
        # the output task finished unexpectedly. Attempt reconnection.
        out_task.cancel()
        try:
            await out_task
        except (asyncio.CancelledError, Exception):
            pass

        stop_spinner()
        ready.set()
        console.print(f"[yellow]Disconnected from {uri}. Reconnecting…[/yellow]")
        try:
            await ws.close()
        except Exception:
            pass

        ws = await _connect_with_retry(uri, console)


def main() -> None:
    parser = argparse.ArgumentParser(description="Curunir WebSocket CLI client")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
