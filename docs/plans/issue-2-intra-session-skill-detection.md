# Plan: Intra-Session Skill Detection

**Issue:** [#2 — Detect and load new skills added at runtime](https://github.com/jalemieux/curunir/issues/2)

## Problem

Skills are discovered via `build_skill_manifest()` which scans `skills/*/SKILL.md`. The manifest is embedded in the system prompt. Currently, `_refresh_manifest()` is called only when a **new session** starts (`session_id not in self.sessions`). This means:

- Within a long-running session, new skills are invisible to the model
- The model can't discover skills added mid-conversation
- `load_skill()` already reads from disk dynamically — the gap is only in **discovery** (the manifest table)

## Approach: Directory mtime-based staleness check

Before each LLM call, compare the `skills_dir` mtime against a stored timestamp. If the directory has been modified (skill added/removed), rebuild the manifest. This is cheap (one `os.stat()` call per turn) and catches all filesystem changes.

### Why not alternatives?

| Approach | Rejected because |
|----------|-----------------|
| File watcher (watchdog) | Adds a dependency, more moving parts, overkill for the change frequency |
| Periodic background refresh | Latency gap — model could miss skills for N seconds; adds concurrency concerns |
| Per-turn unconditional rescan | Unnecessary I/O when nothing changed (multiple `glob` + `read` per turn) |

The mtime approach gives us the best of all worlds: zero-cost when nothing changes, immediate detection when something does.

## Implementation

### Step 1: Track skills directory mtime in Agent

**File:** `src/agent/agent.py`

In `Agent.__init__()`, after building the initial manifest, store the `skills_dir` mtime:

```python
self._skills_dir_mtime: float = self._get_skills_dir_mtime()
```

Add a helper:

```python
def _get_skills_dir_mtime(self) -> float:
    """Return the skills directory mtime, or 0.0 if it doesn't exist."""
    try:
        return self.config.skills_dir.stat().st_mtime
    except (FileNotFoundError, OSError):
        return 0.0
```

### Step 2: Check-and-refresh before each handle

**File:** `src/agent/agent.py`

Replace the current new-session-only refresh logic:

```python
# Current (line 170-171):
if session_id not in self.sessions:
    self._refresh_manifest()
```

With a mtime-based check that runs on every `handle()` call:

```python
self._maybe_refresh_manifest()
```

Where `_maybe_refresh_manifest` is:

```python
def _maybe_refresh_manifest(self) -> None:
    """Rebuild the skill manifest if the skills directory has changed."""
    current_mtime = self._get_skills_dir_mtime()
    if current_mtime != self._skills_dir_mtime:
        self._refresh_manifest()
        self._skills_dir_mtime = current_mtime
```

This is a strict improvement: new sessions still get a fresh manifest (the dir mtime will differ from `0.0` or the init-time value), and existing sessions also get updates when skills change.

### Step 3: Update tests

**File:** `tests/test_agent.py`

1. **Update `test_new_session_picks_up_new_skills`** — behavior is preserved (still works), just driven by mtime now instead of session novelty.

2. **Update `test_same_session_does_not_rescan`** — this test currently asserts that `build_skill_manifest` is NOT called on the second `handle()` within the same session. With the new approach, it still won't be called if no skills were added (mtime unchanged). No change needed — the test should pass as-is.

3. **Add `test_mid_session_picks_up_new_skills`** — new test:
   - Create agent, send message in session `s1`
   - Add a new skill directory to the filesystem
   - Send another message in the **same** session `s1`
   - Assert the system prompt now includes the new skill

4. **Add `test_no_rescan_when_skills_unchanged`** — confirm that when the skills dir hasn't changed, `build_skill_manifest` is not called on subsequent handle calls (even across sessions).

### Step 4: Update `_refresh_manifest` (cleanup)

The standalone `_refresh_manifest()` method remains but is now only called from `_maybe_refresh_manifest()`. No signature change needed.

## Files Changed

| File | Change |
|------|--------|
| `src/agent/agent.py` | Add `_get_skills_dir_mtime()`, `_maybe_refresh_manifest()`, mtime tracking in `__init__`, replace session check in `handle()` |
| `tests/test_agent.py` | Add mid-session detection test, add no-rescan-when-unchanged test |

## Risks & Edge Cases

- **macOS mtime granularity**: HFS+ has 1-second mtime resolution. If a skill is added and a turn happens within the same second, it could be missed until the next turn. This is acceptable for the "eventually detected" requirement.
- **Subdirectory changes**: Modifying a file inside `skills/foo/` updates `foo/`'s mtime but not `skills/`'s. Adding/removing a directory does update `skills/`'s mtime, which is the case we care about (new skills = new directories).
- **Thread safety**: `handle()` is called from a single `agent_worker` coroutine per the current architecture, so no locking needed.
