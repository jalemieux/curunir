# Context Persistence via Git Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist `context/memory/`, `context/schedules.json`, and `context/identity.md` across container restarts and cross-host deployments using git-based sync.

**Architecture:** A new `src/context_sync.py` module wraps git operations on the `context/` directory. On startup it initializes a git repo and pulls from a configured remote. After every write (memory extraction, schedule update), it commits and pushes. A periodic pull task catches changes from other instances. The module uses a module-level singleton pattern — callers import `notify_write()` to trigger sync, avoiding the need to thread a sync object through every function signature.

**Concurrency model:** Single-writer is the expected deployment model. If two instances write concurrently, merge conflicts on `schedules.json` will be silently dropped (the `_run_git` helper returns empty string on failure), and the instance continues with its local state. Multi-writer support would require conflict-aware merging or a different storage format — out of scope for this iteration.

**Event loop note:** `notify_write()` is synchronous (runs `subprocess.run` for git commands). This is acceptable because: (a) local commits are fast (~ms), (b) with a remote, push happens after the response is already sent to the user (memory extraction runs as a background task), and (c) schedule writes already run in a thread pool via `asyncio.to_thread` in the dispatcher. If push latency becomes a problem, `notify_write()` can be wrapped in `asyncio.to_thread()` at async call sites.

**Tech Stack:** Python stdlib (`subprocess`, `asyncio`), git (already in Docker image)

**Import convention note:** Files in `src/` use relative imports (`from .config import ...`). Files in `src/tools/` use absolute imports (`from src.config import ...`). Follow the existing convention of each file being modified.

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/context_sync.py` | Git sync operations: init, commit+push, pull, module-level singleton |
| Create | `tests/test_context_sync.py` | Tests for all sync operations |
| Modify | `src/memory_extractor.py` (`_extract` fn) | Call `notify_write()` after writing facts/summaries |
| Modify | `src/tools/schedule_tool.py` (`_save` fn) | Call `notify_write()` after `_save()` |
| Modify | `run.py` (`main` fn) | Initialize sync on startup, start periodic pull task |
| Modify | `.env.example` | Add `CONTEXT_SYNC_REMOTE`, `CONTEXT_SYNC_BRANCH` docs |
| Modify | `docker-compose.yml` | Add optional SSH key volume mount |

---

### Task 1: Core sync module — `src/context_sync.py`

**Files:**
- Create: `src/context_sync.py`
- Test: `tests/test_context_sync.py`

This task builds the git wrapper and the module-level API. All git operations run as subprocesses in the `context_dir` working directory.

- [ ] **Step 1: Write failing test — `_run_git` helper executes git commands**

```python
# tests/test_context_sync.py
import subprocess
from pathlib import Path

import pytest

from src.context_sync import ContextSync


@pytest.fixture
def sync_dir(tmp_path):
    """A temporary directory initialized as a git repo (uses subdirectory to avoid conflicts with other tmp_path uses)."""
    d = tmp_path / "sync"
    d.mkdir()
    subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=True, capture_output=True)
    return d


def test_run_git_returns_stdout(sync_dir):
    cs = ContextSync(sync_dir)
    result = cs._run_git("status")
    assert "On branch" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_context_sync.py::test_run_git_returns_stdout -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.context_sync'`

- [ ] **Step 3: Write minimal `ContextSync` class with `_run_git`**

```python
# src/context_sync.py
"""Git-based sync for the context directory (memory, schedules, identity)."""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class ContextSync:
    """Wraps git operations on the context directory."""

    def __init__(self, context_dir: Path, remote: str | None = None, branch: str = "main"):
        self.context_dir = context_dir
        self.remote = remote
        self.branch = branch

    def _run_git(self, *args: str) -> str:
        """Run a git command in context_dir. Returns stdout on success, empty string on failure."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.context_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                log.warning("git %s failed: %s", args[0], result.stderr.strip())
                return ""
            return result.stdout
        except Exception:
            log.exception("git %s error", args[0])
            return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_context_sync.py::test_run_git_returns_stdout -v`
Expected: PASS

- [ ] **Step 5: Write failing test — `init()` initializes git repo and pulls**

```python
def test_init_creates_git_repo(tmp_path):
    """init() should git-init a directory that isn't already a repo."""
    cs = ContextSync(tmp_path)
    cs.init()
    assert (tmp_path / ".git").is_dir()


def test_init_pulls_if_remote(sync_dir, tmp_path):
    """init() should add remote and pull if remote is configured."""
    # Create a bare remote with one commit
    remote_dir = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

    # Push a commit to the remote from sync_dir
    (sync_dir / "identity.md").write_text("I am test.")
    subprocess.run(["git", "add", "."], cwd=sync_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=sync_dir, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_dir)], cwd=sync_dir, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=sync_dir, check=True, capture_output=True)

    # Now init a fresh dir with that remote
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    cs = ContextSync(clone_dir, remote=str(remote_dir), branch="main")
    cs.init()

    assert (clone_dir / "identity.md").exists()
    assert (clone_dir / "identity.md").read_text() == "I am test."
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/test_context_sync.py::test_init_creates_git_repo tests/test_context_sync.py::test_init_pulls_if_remote -v`
Expected: FAIL — `AttributeError: 'ContextSync' object has no attribute 'init'`

- [ ] **Step 7: Implement `init()`**

Add to `ContextSync` class:

```python
    def init(self) -> None:
        """Initialize git repo and pull from remote if configured."""
        git_dir = self.context_dir / ".git"

        if not git_dir.is_dir():
            if self.remote:
                # Init + fetch + checkout (works even if context_dir is non-empty, e.g. Docker volume mount)
                self._init_from_remote()
                return
            self._run_git("init")
            self._run_git("config", "user.email", "curunir@localhost")
            self._run_git("config", "user.name", "curunir")
            log.info("initialized local git repo in %s", self.context_dir)
            return

        # Existing repo — pull if remote configured
        if self.remote:
            self._ensure_remote()
            self.pull()

    def _init_from_remote(self) -> None:
        """Initialize repo from remote, works even if context_dir is non-empty (e.g. Docker volume mount)."""
        self._run_git("init")
        self._run_git("config", "user.email", "curunir@localhost")
        self._run_git("config", "user.name", "curunir")
        self._run_git("remote", "add", "origin", self.remote)
        self._run_git("fetch", "origin", self.branch)
        # Checkout remote branch, merging with any local files
        self._run_git("checkout", "-b", self.branch, f"origin/{self.branch}")
        log.info("initialized from remote %s (branch: %s)", self.remote, self.branch)

    def _ensure_remote(self) -> None:
        """Ensure 'origin' points at the configured remote."""
        current = self._run_git("remote", "get-url", "origin").strip()
        if current == self.remote:
            return
        if current:
            self._run_git("remote", "set-url", "origin", self.remote)
        else:
            self._run_git("remote", "add", "origin", self.remote)

    def pull(self) -> None:
        """Pull latest changes from remote."""
        if not self.remote:
            return
        self._run_git("pull", "--rebase=false", "origin", self.branch)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/test_context_sync.py::test_init_creates_git_repo tests/test_context_sync.py::test_init_pulls_if_remote -v`
Expected: PASS

- [ ] **Step 9: Write failing test — `commit_and_push()`**

```python
def test_commit_and_push_commits_changes(sync_dir):
    """commit_and_push() should commit new/changed files."""
    cs = ContextSync(sync_dir)
    (sync_dir / "memory" / "prefs.md").parent.mkdir(parents=True, exist_ok=True)
    (sync_dir / "memory" / "prefs.md").write_text("## Pref\nLikes coffee.")

    cs.commit_and_push()

    log_out = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=sync_dir, capture_output=True, text=True, check=True,
    )
    assert "context update" in log_out.stdout.lower() or len(log_out.stdout.strip().splitlines()) >= 1


def test_commit_and_push_pushes_to_remote(sync_dir, tmp_path):
    """commit_and_push() should push to remote when configured."""
    remote_dir = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_dir)], cwd=sync_dir, check=True, capture_output=True)

    # Initial commit so we have a branch
    (sync_dir / "init.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=sync_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=sync_dir, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=sync_dir, check=True, capture_output=True)

    # Now write and sync
    cs = ContextSync(sync_dir, remote=str(remote_dir), branch="main")
    (sync_dir / "schedules.json").write_text("[]")
    cs.commit_and_push()

    # Verify pushed by cloning
    verify_dir = tmp_path / "verify"
    subprocess.run(["git", "clone", str(remote_dir), str(verify_dir)], check=True, capture_output=True)
    assert (verify_dir / "schedules.json").exists()


def test_commit_and_push_noop_when_clean(sync_dir):
    """commit_and_push() should be a no-op when there are no changes."""
    cs = ContextSync(sync_dir)

    # Make an initial commit
    (sync_dir / "init.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=sync_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=sync_dir, check=True, capture_output=True)

    # No changes — should not error
    cs.commit_and_push()

    log_out = subprocess.run(
        ["git", "log", "--oneline"], cwd=sync_dir, capture_output=True, text=True, check=True,
    )
    assert len(log_out.stdout.strip().splitlines()) == 1  # Only the init commit
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `python -m pytest tests/test_context_sync.py::test_commit_and_push_commits_changes tests/test_context_sync.py::test_commit_and_push_pushes_to_remote tests/test_context_sync.py::test_commit_and_push_noop_when_clean -v`
Expected: FAIL — `AttributeError: 'ContextSync' object has no attribute 'commit_and_push'`

- [ ] **Step 11: Implement `commit_and_push()`**

Add to `ContextSync` class:

```python
    def commit_and_push(self) -> None:
        """Stage all changes, commit, and push if remote is configured."""
        # Check for changes
        status = self._run_git("status", "--porcelain")
        if not status.strip():
            return

        self._run_git("add", "-A")
        self._run_git("commit", "-m", "context update")

        if self.remote:
            self._run_git("push", "origin", self.branch)
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `python -m pytest tests/test_context_sync.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 13: Write failing test — module-level `notify_write()` API**

```python
from unittest.mock import patch, MagicMock

from src.context_sync import init_sync, notify_write, get_sync, _reset


def test_notify_write_calls_commit_and_push(sync_dir):
    _reset()
    sync = init_sync(sync_dir)
    (sync_dir / "test.md").write_text("hello")

    notify_write()

    log_out = subprocess.run(
        ["git", "log", "--oneline"], cwd=sync_dir, capture_output=True, text=True, check=True,
    )
    assert len(log_out.stdout.strip().splitlines()) >= 1


def test_notify_write_noop_when_no_sync():
    _reset()
    # Should not raise
    notify_write()
```

- [ ] **Step 14: Run tests to verify they fail**

Run: `python -m pytest tests/test_context_sync.py::test_notify_write_calls_commit_and_push tests/test_context_sync.py::test_notify_write_noop_when_no_sync -v`
Expected: FAIL — `ImportError: cannot import name 'init_sync'`

- [ ] **Step 15: Implement module-level API**

Add to bottom of `src/context_sync.py`:

```python
_instance: ContextSync | None = None


def init_sync(context_dir: Path, remote: str | None = None, branch: str = "main") -> ContextSync:
    """Initialize the module-level sync instance. Call once at startup."""
    global _instance
    _instance = ContextSync(context_dir, remote=remote, branch=branch)
    _instance.init()
    return _instance


def get_sync() -> ContextSync | None:
    """Return the current sync instance, or None if not initialized."""
    return _instance


def notify_write() -> None:
    """Commit and push context changes. Safe to call when sync is not configured."""
    if _instance is not None:
        _instance.commit_and_push()


def _reset() -> None:
    """Reset module state. For testing only."""
    global _instance
    _instance = None
```

- [ ] **Step 16: Run all context_sync tests**

Run: `python -m pytest tests/test_context_sync.py -v`
Expected: PASS

- [ ] **Step 17: Add .gitignore creation to init()**

When `init()` creates a new git repo, also write a `.gitignore` to exclude temp files from atomic writes:

```python
    def _write_gitignore(self) -> None:
        """Create .gitignore to exclude temp files from atomic writes."""
        gitignore = self.context_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.tmp\n*.swp\n*~\n")
```

Call `self._write_gitignore()` in `init()` after `self._run_git("init")` and in `_init_from_remote()` after checkout.

- [ ] **Step 18: Commit**

```bash
git add src/context_sync.py tests/test_context_sync.py
git commit -m "feat: add context_sync module with git-based persistence"
```

---

### Task 2: Hook sync into memory extractor

**Files:**
- Modify: `src/memory_extractor.py:54-102`
- Test: `tests/test_memory_extractor.py`

After `_extract()` writes facts and summaries, call `notify_write()` to commit and push the changes.

- [ ] **Step 1: Write failing test — extraction triggers sync**

Add to `tests/test_memory_extractor.py`:

```python
@pytest.mark.asyncio
async def test_calls_notify_write_after_extraction(agent_config):
    """Should call context_sync.notify_write() after writing facts."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [{"file": "preferences.md", "content": "## Test\n**Fact:** likes testing"}],
            "summary": {"topic_slug": "test", "content": "Summary."},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response), \
         patch("src.memory_extractor.notify_write") as mock_notify:
        await extract_learnings(agent_config, _history())
        mock_notify.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_extractor.py::test_calls_notify_write_after_extraction -v`
Expected: FAIL — `AttributeError: <module 'src.memory_extractor'> does not have the attribute 'notify_write'`

- [ ] **Step 3: Add notify_write call to memory_extractor**

In `src/memory_extractor.py`, add import and call:

```python
# Add at top, after existing imports:
from .context_sync import notify_write

# At the end of _extract(), after writing facts and summary (after line 102):
    notify_write()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_extractor.py -v`
Expected: PASS (all tests including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/memory_extractor.py tests/test_memory_extractor.py
git commit -m "feat: trigger context sync after memory extraction"
```

---

### Task 3: Hook sync into schedule tool

**Files:**
- Modify: `src/tools/schedule_tool.py:25-38`
- Test: `tests/test_schedule_tool.py`

After `_save()` writes schedules.json, call `notify_write()`.

- [ ] **Step 1: Write failing test — schedule save triggers sync**

Add to `tests/test_schedule_tool.py`:

```python
from unittest.mock import patch


class TestScheduleSync:
    def test_add_triggers_notify_write(self, agent_config, schedule_file):
        with patch("src.tools.schedule_tool.notify_write") as mock_notify:
            exec_schedule({
                "action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p",
            }, agent_config)
            mock_notify.assert_called_once()

    def test_update_triggers_notify_write(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        with patch("src.tools.schedule_tool.notify_write") as mock_notify:
            exec_schedule({"action": "update", "id": "t1", "prompt": "new"}, agent_config)
            mock_notify.assert_called_once()

    def test_remove_triggers_notify_write(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        with patch("src.tools.schedule_tool.notify_write") as mock_notify:
            exec_schedule({"action": "remove", "id": "t1"}, agent_config)
            mock_notify.assert_called_once()

    def test_list_does_not_trigger_notify_write(self, agent_config, schedule_file):
        with patch("src.tools.schedule_tool.notify_write") as mock_notify:
            exec_schedule({"action": "list"}, agent_config)
            mock_notify.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_schedule_tool.py::TestScheduleSync -v`
Expected: FAIL — `AttributeError: <module 'src.tools.schedule_tool'> does not have the attribute 'notify_write'`

- [ ] **Step 3: Add notify_write call to schedule_tool**

In `src/tools/schedule_tool.py`, add import and call:

```python
# Add at top, after existing imports:
from src.context_sync import notify_write

# In _save(), after the os.rename line (after line 32):
    notify_write()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_schedule_tool.py -v`
Expected: PASS (all tests including new sync tests)

- [ ] **Step 5: Commit**

```bash
git add src/tools/schedule_tool.py tests/test_schedule_tool.py
git commit -m "feat: trigger context sync after schedule changes"
```

---

### Task 4: Wire sync into startup and periodic pull

**Files:**
- Modify: `run.py:147-203`

Initialize the sync on startup and add a periodic pull task to the TaskGroup.

- [ ] **Step 1: Add sync initialization and periodic pull to run.py**

```python
# Add import at top of run.py:
from src.context_sync import init_sync

# In main(), after config is created (after line 164), add:
    context_remote = os.environ.get("CONTEXT_SYNC_REMOTE")
    context_branch = os.environ.get("CONTEXT_SYNC_BRANCH", "main")
    sync = init_sync(config.context_dir, remote=context_remote, branch=context_branch)
    if context_remote:
        logger.info("Context sync enabled: %s (branch: %s)", context_remote, context_branch)

# Add a periodic_pull coroutine near periodic_extraction:
async def periodic_pull(sync_instance, interval_sec: int = 300):
    """Periodically pull context changes from remote."""
    if not sync_instance or not sync_instance.remote:
        return
    while True:
        await asyncio.sleep(interval_sec)
        await asyncio.to_thread(sync_instance.pull)

# In the TaskGroup block, add:
        tg.create_task(periodic_pull(sync))
```

- [ ] **Step 2: Write test for periodic_pull coroutine**

Add to `tests/test_context_sync.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_periodic_pull_calls_pull_on_interval():
    """periodic_pull() should call sync.pull() after each interval."""
    from run import periodic_pull

    mock_sync = MagicMock()
    mock_sync.remote = "git@example.com:repo.git"
    mock_sync.pull = MagicMock()

    call_count = 0
    original_sleep = asyncio.sleep

    async def fake_sleep(secs):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()  # Break the loop after 2 iterations
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        with pytest.raises(asyncio.CancelledError):
            await periodic_pull(mock_sync, interval_sec=1)

    assert mock_to_thread.call_count >= 1


@pytest.mark.asyncio
async def test_periodic_pull_returns_when_no_remote():
    """periodic_pull() should return immediately when sync has no remote."""
    from run import periodic_pull

    mock_sync = MagicMock()
    mock_sync.remote = None

    # Should return without blocking
    await periodic_pull(mock_sync, interval_sec=1)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/test_context_sync.py -v && python -m pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 4: Commit**

```bash
git add run.py tests/test_context_sync.py
git commit -m "feat: initialize context sync on startup with periodic pull"
```

---

### Task 5: Configuration and documentation

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add env var documentation to .env.example**

Append to `.env.example`:

```bash
# Context persistence (git-based sync)
# Syncs context/memory, schedules.json, and identity.md to a git remote.
# Requires the remote to be accessible (SSH key or HTTPS token).
# CONTEXT_SYNC_REMOTE=git@github.com:you/curunir-context.git
# CONTEXT_SYNC_BRANCH=main
```

- [ ] **Step 2: Add optional SSH key mount to docker-compose.yml**

Add a comment and optional volume for SSH keys:

```yaml
    volumes:
      - ./secrets:/secrets:ro
      - ./workspace:/app/workspace
      - ./context:/app/context
      # Uncomment to enable git-based context sync via SSH:
      # - ~/.ssh:/root/.ssh:ro
```

- [ ] **Step 3: Commit**

```bash
git add .env.example docker-compose.yml
git commit -m "docs: add context sync configuration to env example and compose"
```

---

### Task 6: Full integration test and cleanup

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify no lint issues**

Run: `python -m py_compile src/context_sync.py && echo OK`
Expected: OK

- [ ] **Step 3: Final commit if any cleanup needed**
