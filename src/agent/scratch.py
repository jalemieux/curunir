"""Ephemeral "Scratch" conversation marker.

The portal exposes a single pinned slot for quick, throwaway chats —
"polish this message", "what's the difference between X and Y", etc. The
slot uses a fixed session id so the rest of the system can detect it and
opt out of persistence, memory extraction, and the sidebar listing.

Centralized here so every site that needs to distinguish scratch traffic
imports the same constant — no string literals scattered across the
codebase.
"""
from __future__ import annotations

SCRATCH_SESSION_ID = "scratch"


def is_scratch(session_id: str | None) -> bool:
    """True for the singleton scratch session id; False for anything else."""
    return session_id == SCRATCH_SESSION_ID
