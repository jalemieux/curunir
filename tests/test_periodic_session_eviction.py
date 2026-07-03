"""Tests for run.periodic_session_eviction — the background sweep that
evicts idle in-memory sessions (issue #495)."""
import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_periodic_session_eviction_calls_evict_idle_sessions():
    """One tick → agent.evict_idle_sessions() is invoked."""
    from run import periodic_session_eviction

    mock_agent = MagicMock()
    called = asyncio.Event()

    def evict():
        called.set()
        return 0

    mock_agent.evict_idle_sessions.side_effect = evict

    task = asyncio.create_task(periodic_session_eviction(mock_agent, interval_sec=0))
    try:
        await asyncio.wait_for(called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_agent.evict_idle_sessions.assert_called()
