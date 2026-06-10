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
