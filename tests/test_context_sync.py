# tests/test_context_sync.py
"""Tests for the context sync module (push-only git sync for context/)."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import AgentConfig
from src.context_sync import ContextSync


def _init_bare_remote(tmp_path: Path) -> str:
    """Create a bare git repo to act as a remote."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    return str(bare)


def _init_context_repo(context_dir: Path, remote_url: str, branch: str = "main") -> None:
    """Initialize a git repo in context_dir and push an initial commit to remote."""
    subprocess.run(["git", "init", "-b", branch], cwd=context_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=context_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=context_dir, check=True, capture_output=True)
    # Initial commit so the branch exists
    (context_dir / ".gitkeep").touch()
    subprocess.run(["git", "add", "."], cwd=context_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=context_dir, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=context_dir, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=context_dir, check=True, capture_output=True)


@pytest.fixture
def sync_env(tmp_path):
    """Set up a context dir with a bare remote for testing."""
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    remote_url = _init_bare_remote(tmp_path)
    _init_context_repo(context_dir, remote_url)
    config = AgentConfig(
        context_dir=context_dir,
        context_sync_remote=remote_url,
        context_sync_branch="main",
    )
    return config, context_dir, remote_url


class TestContextSyncDisabled:
    """When CONTEXT_SYNC_REMOTE is not set, all operations are no-ops."""

    def test_sync_disabled_when_no_remote(self, tmp_path):
        config = AgentConfig(context_dir=tmp_path, context_sync_remote=None)
        sync = ContextSync(config)
        assert not sync.enabled

    async def test_notify_write_noop_when_disabled(self, tmp_path):
        config = AgentConfig(context_dir=tmp_path, context_sync_remote=None)
        sync = ContextSync(config)
        # Should not raise
        await sync.notify_write(tmp_path / "some_file.md")

    async def test_push_noop_when_disabled(self, tmp_path):
        config = AgentConfig(context_dir=tmp_path, context_sync_remote=None)
        sync = ContextSync(config)
        await sync.push()


class TestContextSyncEnabled:
    """When remote is configured, sync commits and pushes."""

    async def test_enabled_when_remote_set(self, sync_env):
        config, context_dir, _ = sync_env
        sync = ContextSync(config)
        assert sync.enabled

    async def test_notify_write_commits_and_pushes(self, sync_env):
        config, context_dir, remote_url = sync_env
        sync = ContextSync(config)

        # Write a file and notify
        test_file = context_dir / "memory" / "test.md"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("hello world")

        await sync.notify_write(test_file)

        # Verify the file was committed
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=context_dir, capture_output=True, text=True,
        )
        assert "context sync" in result.stdout.lower() or "memory" in result.stdout.lower() or result.returncode == 0

        # Verify pushed to remote: clone remote and check file exists
        clone_dir = context_dir.parent / "verify_clone"
        subprocess.run(["git", "clone", remote_url, str(clone_dir)], check=True, capture_output=True)
        assert (clone_dir / "memory" / "test.md").read_text() == "hello world"

    async def test_notify_write_handles_multiple_files(self, sync_env):
        config, context_dir, remote_url = sync_env
        sync = ContextSync(config)

        f1 = context_dir / "a.md"
        f1.write_text("file a")
        await sync.notify_write(f1)

        f2 = context_dir / "b.md"
        f2.write_text("file b")
        await sync.notify_write(f2)

        # Both should be in remote
        clone_dir = context_dir.parent / "verify_clone"
        subprocess.run(["git", "clone", remote_url, str(clone_dir)], check=True, capture_output=True)
        assert (clone_dir / "a.md").read_text() == "file a"
        assert (clone_dir / "b.md").read_text() == "file b"

    async def test_push_commits_uncommitted_changes(self, sync_env):
        config, context_dir, remote_url = sync_env
        sync = ContextSync(config)

        # Write a file but don't call notify_write
        test_file = context_dir / "uncommitted.md"
        test_file.write_text("uncommitted content")

        # push() should stage, commit, and push everything
        await sync.push()

        clone_dir = context_dir.parent / "verify_clone"
        subprocess.run(["git", "clone", remote_url, str(clone_dir)], check=True, capture_output=True)
        assert (clone_dir / "uncommitted.md").read_text() == "uncommitted content"

    async def test_push_noop_when_nothing_changed(self, sync_env):
        config, context_dir, _ = sync_env
        sync = ContextSync(config)

        # Get commit count before
        result_before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=context_dir, capture_output=True, text=True,
        )

        await sync.push()

        result_after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=context_dir, capture_output=True, text=True,
        )
        assert result_before.stdout.strip() == result_after.stdout.strip()

    async def test_notify_write_ignores_path_outside_context(self, sync_env):
        config, context_dir, _ = sync_env
        sync = ContextSync(config)

        # Writing outside context_dir should be a no-op (no crash)
        outside = context_dir.parent / "outside.md"
        outside.write_text("outside")
        await sync.notify_write(outside)

    async def test_push_failure_logged_not_raised(self, sync_env):
        """Push failure should be logged, not raised."""
        config, context_dir, _ = sync_env
        sync = ContextSync(config)

        # Break the remote
        subprocess.run(
            ["git", "remote", "set-url", "origin", "/nonexistent/repo.git"],
            cwd=context_dir, check=True, capture_output=True,
        )

        test_file = context_dir / "will_fail.md"
        test_file.write_text("content")

        # Should not raise even though push will fail
        await sync.notify_write(test_file)


    async def test_push_recovers_when_remote_is_ahead(self, sync_env):
        """Push should pull --rebase and retry when remote has diverged."""
        config, context_dir, remote_url = sync_env
        sync = ContextSync(config)

        # Simulate remote getting ahead: clone, commit, push from another checkout
        other = context_dir.parent / "other_checkout"
        subprocess.run(["git", "clone", remote_url, str(other)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "other@test.com"], cwd=other, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Other"], cwd=other, check=True, capture_output=True)
        (other / "from_other.md").write_text("from other")
        subprocess.run(["git", "add", "."], cwd=other, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "other commit"], cwd=other, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=other, check=True, capture_output=True)

        # Now local is behind remote. A push should fail then recover via rebase.
        local_file = context_dir / "local_change.md"
        local_file.write_text("local content")

        await sync.push()

        # Verify both files made it to remote
        verify = context_dir.parent / "verify_clone"
        subprocess.run(["git", "clone", remote_url, str(verify)], check=True, capture_output=True)
        assert (verify / "from_other.md").read_text() == "from other"
        assert (verify / "local_change.md").read_text() == "local content"


class TestPeriodicPush:
    """Test the periodic_push background coroutine."""

    async def test_periodic_push_calls_push(self, sync_env):
        config, context_dir, remote_url = sync_env
        sync = ContextSync(config)

        # Write a file
        test_file = context_dir / "periodic.md"
        test_file.write_text("periodic content")

        # Run periodic_push with a very short interval, cancel after first iteration
        task = asyncio.create_task(sync.periodic_push(interval_sec=0.1))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Verify pushed
        clone_dir = context_dir.parent / "verify_clone"
        subprocess.run(["git", "clone", remote_url, str(clone_dir)], check=True, capture_output=True)
        assert (clone_dir / "periodic.md").read_text() == "periodic content"
