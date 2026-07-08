"""Helpers shared by the agent- and browser-facing WebSocket endpoints."""

from fastapi import WebSocket


def bearer_from_headers(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization") or ws.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
