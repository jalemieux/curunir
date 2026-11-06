"""Helpers for the optional ``agent`` field on channel frames.

A frame may name the agent (in a multi-agent container) that should handle
it; a missing/empty field means the default agent, which is what every
pre-multi-agent client sends. Providers and the cancel callback are called
with ``agent=`` only when a frame carried one, so the single-argument
callables that tests and older wiring pass keep working unchanged.
"""
from __future__ import annotations

from typing import Any, Callable


def agent_of(payload: dict | None) -> str | None:
    """The ``agent`` named by a frame, or ``None`` for the default agent."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("agent")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def call_provider(fn: Callable[..., Any], *args, agent: str | None = None) -> Any:
    """Call a channel provider, forwarding ``agent=`` only when one is set."""
    if agent is None:
        return fn(*args)
    return fn(*args, agent=agent)


def request_cancel(
    cancel_session: Callable[..., bool] | None, session_id: str, agent: str | None = None,
    played_seq: int | None = None,
) -> bool:
    """Route an interrupt to the cancel callback, with the agent when known.

    ``played_seq`` is the last ``audio_chunk`` the client played in full, for a
    voice turn being interrupted; a callback that does not take it (a bare
    ``Agent.request_cancel``) is called without it."""
    if cancel_session is None:
        return False
    kwargs = {}
    if agent is not None:
        kwargs["agent"] = agent
    if isinstance(played_seq, bool):
        played_seq = None
    elif isinstance(played_seq, (int, float, str)):
        try:
            played_seq = int(played_seq)
        except ValueError:
            played_seq = None
    else:
        played_seq = None
    if played_seq is not None:
        kwargs["played_seq"] = played_seq
    try:
        return bool(cancel_session(session_id, **kwargs))
    except TypeError:
        if not kwargs:
            raise
        return bool(cancel_session(session_id, **({"agent": agent} if agent is not None else {})))
