# src/context_sync.py
"""Push-only git sync for the context directory.

When CONTEXT_SYNC_REMOTE is configured, this module commits and pushes
changes to a dedicated context git repo after writes. When unconfigured,
all operations are silent no-ops.
"""

import asyncio
import logging
import subprocess
from pathlib import Path

from .config import AgentConfig

log = logging.getLogger(__name__)


class ContextSync:
    def __init__(self, config: AgentConfig) -> None:
        self._context_dir = config.context_dir.resolve()
        self._remote = config.context_sync_remote
        self._branch = config.context_sync_branch

    @property
    def enabled(self) -> bool:
        return self._remote is not None

    async def notify_write(self, path: Path) -> None:
        """Stage, commit, and push after a write to *path*."""
        if not self.enabled:
            return
        try:
            resolved = path.resolve()
            if not str(resolved).startswith(str(self._context_dir)):
                log.debug("notify_write: path %s is outside context dir, skipping", path)
                return
            rel = resolved.relative_to(self._context_dir)
            await self._run_git("add", "--", str(rel))
            await self._commit(f"context sync: {rel}")
            await self._push()
        except Exception:
            log.exception("context sync failed for %s", path)

    async def push(self) -> None:
        """Stage all changes, commit if dirty, and push (safety-net call)."""
        if not self.enabled:
            return
        try:
            await self._run_git("add", "-A")
            await self._commit("context sync: periodic")
            await self._push()
        except Exception:
            log.exception("context sync periodic push failed")

    async def periodic_push(self, interval_sec: int = 300) -> None:
        """Background loop that pushes uncommitted changes on an interval."""
        while True:
            await asyncio.sleep(interval_sec)
            await self.push()

    # -- internal helpers --

    async def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        return await asyncio.to_thread(
            subprocess.run,
            ["git", *args],
            cwd=self._context_dir,
            capture_output=True,
            text=True,
        )

    async def _commit(self, message: str) -> None:
        # Check if there's anything staged
        result = await self._run_git("diff", "--cached", "--quiet")
        if result.returncode == 0:
            # Nothing staged
            return
        await self._run_git("commit", "-m", message)

    async def _push(self) -> None:
        result = await self._run_git("push", "origin", self._branch)
        if result.returncode != 0:
            log.warning("git push failed: %s", result.stderr.strip())
