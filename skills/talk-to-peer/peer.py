#!/usr/bin/env python3
"""Talk to another curunir instance over its WebSocket channel.

Reads peer connection info from the CURUNIR_PEERS env var (JSON), connects to
a named peer as a WS client, sends one message, and prints the peer's final
reply. The agent invokes this via the bash tool; the conversational loop is the
agent's own reasoning loop. No server-side changes are required — the peer
just sees a normal user on its WebSocket channel.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import websockets
import websockets.exceptions

# Match the server's max_size so attachment-bearing frames aren't rejected.
_MAX_SIZE = 32 * 1024 * 1024
_DEFAULT_TIMEOUT = 120.0


class PeerError(Exception):
    """Raised for any user-facing failure (bad config, unknown peer, etc.)."""


def parse_peers(raw: str | None) -> dict:
    """Parse the CURUNIR_PEERS JSON value into a dict.

    Returns {} when unset/empty. Raises PeerError on malformed JSON or shape.
    """
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PeerError(f"CURUNIR_PEERS is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PeerError(
            "CURUNIR_PEERS must be a JSON object of name -> {url, token}"
        )
    for name, info in data.items():
        if not isinstance(info, dict) or "url" not in info:
            raise PeerError(
                f"peer {name!r} must be an object with at least a 'url'"
            )
    return data


def peer_names(peers: dict) -> list[str]:
    """Return sorted peer names (never urls/tokens)."""
    return sorted(peers)


def resolve_peer(peers: dict, name: str) -> dict:
    """Return connection info for *name*, or raise listing available names."""
    if name not in peers:
        available = ", ".join(peer_names(peers)) or "(none configured)"
        raise PeerError(f"unknown peer {name!r}; available: {available}")
    return peers[name]


async def send_to_peer(
    url: str,
    token: str | None,
    session_id: str,
    message: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Connect to *url*, send *message*, return the peer's final reply text.

    Sends a hello frame (with *token* and a stable *session_id* so the peer
    keeps one continuing conversation across the per-message reconnects), then
    a content frame, then reads frames until one carries ``final: true``,
    accumulating streamed ``delta`` chunks. Falls back to the final frame's
    ``content`` for non-streaming servers. Raises PeerError on timeout,
    connection failure, or a close before any final frame.
    """
    async def _converse() -> str:
        async with websockets.connect(url, max_size=_MAX_SIZE) as ws:
            hello: dict = {"type": "hello", "session_id": session_id}
            if token:
                hello["token"] = token
            await ws.send(json.dumps(hello))
            await ws.send(json.dumps({"content": message}))

            parts: list[str] = []
            async for raw in ws:
                data = json.loads(raw)
                if data.get("type") == "hello":
                    continue
                if data.get("delta"):
                    parts.append(data.get("content") or "")
                    continue
                if data.get("final"):
                    text = "".join(parts).strip()
                    return text or (data.get("content") or "").strip()
            raise PeerError("connection closed before a final reply")

    try:
        return await asyncio.wait_for(_converse(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise PeerError(f"no final reply within {timeout:.0f}s") from exc
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        raise PeerError(f"connection to {url} failed: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Talk to another curunir instance (a configured peer).",
    )
    p.add_argument(
        "--list", action="store_true",
        help="List configured peer names and exit.",
    )
    p.add_argument(
        "--peer", help="Name of the peer to message (from CURUNIR_PEERS).",
    )
    p.add_argument(
        "message", nargs="?", help="Message text to send to the peer.",
    )
    p.add_argument(
        "--session",
        help="Override the session id sent to the peer "
             "(default: peer:<self-name>).",
    )
    p.add_argument(
        "--self-name",
        default=os.environ.get("CURUNIR_SELF_NAME", "curunir"),
        help="This instance's label, used to derive the peer session id.",
    )
    p.add_argument(
        "--timeout", type=float, default=_DEFAULT_TIMEOUT,
        help="Seconds to wait for the peer's final reply.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        peers = parse_peers(os.environ.get("CURUNIR_PEERS"))
        if args.list:
            names = peer_names(peers)
            print("\n".join(names) if names else "(no peers configured)")
            return 0
        if not args.peer or not args.message:
            raise PeerError(
                'usage: peer.py --peer <name> "<message>"  (or --list)'
            )
        info = resolve_peer(peers, args.peer)
        session_id = args.session or f"peer:{args.self_name}"
        reply = asyncio.run(send_to_peer(
            url=info["url"],
            token=info.get("token"),
            session_id=session_id,
            message=args.message,
            timeout=args.timeout,
        ))
        print(reply)
        return 0
    except PeerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
