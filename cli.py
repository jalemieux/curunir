"""Standalone WebSocket CLI client for Curunir.

Usage:
    python cli.py [--host localhost] [--port 8765] [--download-dir DIR]
"""
import argparse
import asyncio
import base64
import json
import logging
import mimetypes
import os
import re

import websockets
import websockets.exceptions
from prompt_toolkit import ANSI, PromptSession
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

logger = logging.getLogger(__name__)

# Reconnection settings
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 30.0

_DEFAULT_DOWNLOAD_DIR = os.path.expanduser("~/Downloads/curunir")


def _detect_version() -> str:
    """Return a short git SHA + dirty marker, or 'dev' if unavailable.

    Run at CLI startup so a visible banner makes it obvious whether the
    running process matches the checkout on disk.
    """
    try:
        import subprocess
        repo = os.path.dirname(os.path.abspath(__file__))
        sha = subprocess.check_output(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        # Only flag dirty for modified tracked files — ignore untracked cruft.
        dirty = subprocess.call(
            ["git", "-C", repo, "diff", "--quiet", "HEAD"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) != 0
        return f"{sha}{'+' if dirty else ''}"
    except Exception:
        return "dev"


def _save_attachment(att: dict, download_dir: str) -> str | None:
    """Decode base64 *data* and write it under *download_dir*.

    Returns the saved local path, or None on failure. Adds a numeric suffix
    (`name-1.pdf`, `name-2.pdf`, ...) when the target name already exists.
    """
    try:
        os.makedirs(download_dir, exist_ok=True)
        filename = att.get("filename") or "attachment"
        # Strip path components so a malicious server can't escape download_dir
        filename = os.path.basename(filename)
        base, ext = os.path.splitext(filename)
        target = os.path.join(download_dir, filename)
        counter = 1
        while os.path.exists(target):
            target = os.path.join(download_dir, f"{base}-{counter}{ext}")
            counter += 1
        with open(target, "wb") as f:
            f.write(base64.b64decode(att["data"]))
        return target
    except (OSError, ValueError) as exc:
        logger.warning("Failed to save attachment %s: %s", att.get("filename"), exc)
        return None

# Size caps mirrored from src/channels/ws.py
_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})


def _guess_mime(path: str) -> str | None:
    """Best-effort MIME detection: guess_type, then UTF-8 sniff as tiebreaker."""
    mime, _ = mimetypes.guess_type(path)
    if mime and mime != "application/octet-stream":
        return mime
    try:
        with open(path, "rb") as f:
            f.read(4096).decode("utf-8")
        return "text/plain"
    except (OSError, UnicodeDecodeError):
        return None


class Staging:
    """Client-side staged attachments.

    `add(path)` returns None on success, or an error string. Enforces the
    same caps as ws.py so we fail fast without a round-trip.
    """

    def __init__(self) -> None:
        self._items: list[dict] = []  # each: {filename, mime_type, data, size}

    @property
    def total_bytes(self) -> int:
        return sum(it["size"] for it in self._items)

    def add(self, path: str) -> str | None:
        if not os.path.isfile(path):
            return f"{os.path.basename(path)}: file not found"
        size = os.path.getsize(path)
        mime = _guess_mime(path)
        if mime is None:
            return f"{os.path.basename(path)}: not a recognized image and not UTF-8 decodable"

        if mime.startswith("image/"):
            if mime not in _ALLOWED_IMAGE_MIMES:
                return f"{os.path.basename(path)}: unsupported image type {mime}"
            if size > _MAX_IMAGE_BYTES:
                return (f"{os.path.basename(path)} is "
                        f"{size / 1024 / 1024:.1f} MB (image cap is 5 MB)")
        else:
            if size > _MAX_TEXT_BYTES:
                return (f"{os.path.basename(path)} is "
                        f"{size / 1024:.0f} KB (text cap is 256 KB)")

        if self.total_bytes + size > _MAX_TOTAL_BYTES:
            return "total staged would exceed 20 MB"

        with open(path, "rb") as f:
            raw = f.read()
        self._items.append({
            "filename": os.path.basename(path),
            "mime_type": mime,
            "data": base64.b64encode(raw).decode(),
            "size": size,
        })
        return None

    def remove(self, index_1_based: int) -> str | None:
        if not 1 <= index_1_based <= len(self._items):
            return f"index {index_1_based} out of range (have {len(self._items)})"
        self._items.pop(index_1_based - 1)
        return None

    def clear(self) -> None:
        self._items.clear()

    def list_display(self) -> str:
        if not self._items:
            return "no files staged"
        parts = []
        for i, it in enumerate(self._items, start=1):
            size = it["size"]
            if size >= 1024 * 1024:
                s = f"{size / 1024 / 1024:.1f} MB"
            else:
                s = f"{size / 1024:.0f} KB"
            parts.append(f"[{i}] {it['filename']} ({s})")
        return "staged: " + ", ".join(parts)

    def to_payload(self) -> list[dict]:
        """Return the wire-format list: {filename, mime_type, data} items."""
        return [
            {"filename": it["filename"], "mime_type": it["mime_type"], "data": it["data"]}
            for it in self._items
        ]


# Matches an absolute POSIX path: `/` followed by one or more non-whitespace
# chars or backslash-escaped chars (shell-style drag-drop uses `\<space>`).
_PATH_RE = re.compile(r'/(?:[^\s\\]|\\.)+')


def _format_size(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def _extract_and_stage_paths(text: str, staging: Staging, console: Console) -> str:
    """Auto-stage absolute paths to existing local files from *text*.

    Returns the text with successfully-staged paths removed. Paths that don't
    resolve to an existing file, or that fail Staging validation, stay in place.
    Makes drag-drop "just work": a dragged file's path becomes an attachment,
    not a literal string the remote agent can't resolve.
    """
    kept: list[tuple[int, int]] = []  # spans to remove (start, end) if staged
    for m in _PATH_RE.finditer(text):
        escaped = m.group(0)
        path = re.sub(r"\\(.)", r"\1", escaped)  # unescape shell backslashes
        if not os.path.isfile(path):
            continue
        err = staging.add(path)
        if err is None:
            item = staging._items[-1]
            console.print(
                f"[dim]\U0001f4ce staged: {item['filename']} ({_format_size(item['size'])})[/dim]"
            )
            kept.append((m.start(), m.end()))
        else:
            console.print(f"[red]❌ rejected: {err}[/red]")

    if not kept:
        return text

    out = []
    last = 0
    for start, end in kept:
        out.append(text[last:start])
        last = end
    out.append(text[last:])
    cleaned = "".join(out)
    # Collapse whitespace left behind when paths are removed.
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned


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


async def run(host: str, port: int, console: Console | None = None,
              download_dir: str = _DEFAULT_DOWNLOAD_DIR) -> None:
    console = console or Console()
    uri = f"ws://{host}:{port}"

    console.print(f"[bold]Curunir[/bold] [dim]({uri})[/dim] [dim]rev {_detect_version()}[/dim]")
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
                # Convert the trailing tool-call ├─ line to ╯─ before printing
                # the answer, so the marker sits above the response.
                streamed_text = flush_stream()
                if streamed_text.strip():
                    if verbose:
                        flush_tool_calls()
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
                        if att.get("path"):
                            line.append(f" \u2192 {att['path']}", style="dim")
                        if att.get("error"):
                            line.append(f" [{att['error']}]", style="red")
                        console.print(line)
                        if att.get("content"):
                            console.print(Markdown(att["content"]))
                        elif att.get("data"):
                            saved = _save_attachment(att, download_dir)
                            saved_line = Text()
                            saved_line.append("     \u21f3 ", style="dim")
                            if saved:
                                saved_line.append("saved to ", style="dim")
                                saved_line.append(saved, style="green")
                            else:
                                saved_line.append("save failed", style="red")
                            console.print(saved_line)

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
    session: PromptSession = PromptSession()
    prompt_text = ANSI("\x1b[1;32m> \x1b[0m")
    ws = await _connect_with_retry(uri, console)

    staging = Staging()

    # A payload that failed to send (due to connection drop) and should be
    # retried on the next connection.
    pending_payload: dict | None = None

    while True:
        # Launch output reader for the current connection
        out_task = asyncio.create_task(output_loop(ws))

        # Give the server a brief window to send its welcome before we show
        # the prompt, so "model: …" renders above the input line. If no
        # welcome arrives (e.g. server has no MODEL set, or a bare test
        # server), proceed after a short timeout so we don't deadlock.
        try:
            await asyncio.wait_for(ready.wait(), timeout=0.25)
        except asyncio.TimeoutError:
            ready.set()

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

                    # Native async prompt: bracketed paste keeps multi-line
                    # pastes as one input, and Ctrl-C raises cleanly.
                    try:
                        line = await session.prompt_async(prompt_text)
                    except EOFError:
                        # Ctrl-D: close cleanly and exit
                        out_task.cancel()
                        await ws.close()
                        return

                    text = line.strip()
                    if not text and not staging.to_payload():
                        continue

                    if text == "/verbose":
                        verbose = not verbose
                        state = "on" if verbose else "off"
                        console.print(f"[dim]Verbose mode {state}.[/dim]")
                        continue

                    # /attach family — all local, never sent over the wire.
                    if text.startswith("/attach"):
                        rest = text[len("/attach"):].strip()
                        if rest == "":
                            console.print(f"[dim]\U0001f4ce {staging.list_display()}[/dim]")
                            continue
                        if rest == "clear":
                            staging.clear()
                            console.print("[dim]staging cleared[/dim]")
                            continue
                        import shlex
                        try:
                            paths = shlex.split(rest)
                        except ValueError as e:
                            console.print(f"[red]❌ {e}[/red]")
                            continue
                        for p in paths:
                            err = staging.add(p)
                            if err:
                                console.print(f"[red]❌ rejected: {err}[/red]")
                            else:
                                item = staging._items[-1]
                                size = item["size"]
                                sstr = (
                                    f"{size / 1024 / 1024:.1f} MB"
                                    if size >= 1024 * 1024
                                    else f"{size / 1024:.0f} KB"
                                )
                                console.print(f"[dim]\U0001f4ce staged: {item['filename']} ({sstr})[/dim]")
                        continue

                    if text.startswith("/detach"):
                        rest = text[len("/detach"):].strip()
                        try:
                            idx = int(rest)
                        except ValueError:
                            console.print("[red]usage: /detach <index>[/red]")
                            continue
                        err = staging.remove(idx)
                        if err:
                            console.print(f"[red]❌ {err}[/red]")
                        else:
                            console.print("[dim]detached[/dim]")
                        continue

                    if text in ("/clear", "/new"):
                        staging.clear()
                        payload = {"content": "", "command": "clear"}
                    elif text == "/reset":
                        staging.clear()
                        payload = {"content": "", "command": "reset"}
                    else:
                        # Auto-stage any absolute file paths in the message
                        # (e.g. from drag-drop onto the terminal).
                        text = _extract_and_stage_paths(text, staging, console)
                        payload = {
                            "content": text,
                            "command": None,
                            "attachments": staging.to_payload() or None,
                        }
                        staging.clear()

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
