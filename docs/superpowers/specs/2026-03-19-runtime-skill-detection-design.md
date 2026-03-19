# Runtime Skill Detection Design

**Date:** 2026-03-19
**Issue:** #2 — Detect and load new skills added at runtime

## Problem

Skills are discovered once at `Agent.__init__()` via `build_skill_manifest()`. The resulting manifest is baked into `self.static_prompt`. If a new skill directory is added to `skills/` while the server is running, it won't appear in the manifest until the process restarts.

## Decision

Rebuild the skill manifest on new session creation. The trigger is `session_id not in self.sessions` in `Agent.handle()`, which naturally fires once per conversation.

### Why not other approaches?

- **Per-call rebuild:** Unnecessary I/O on every message. The requirement is "new conversations should see new skills", not "mid-conversation detection".
- **File-system watcher:** Adds a dependency and platform-specific complexity for no benefit over the session-boundary approach.

## Design

### Changes to `Agent` (`src/agent/agent.py`)

**`__init__`:** Replace `self.static_prompt` (a single frozen string) with two cached fields:
- `self._identity: str` — identity file content, read once at init, never changes.
- `self._skill_manifest: str` — built at init via `build_skill_manifest()`, rebuilt on new sessions.

**`_refresh_manifest()`** — new private method. Calls `build_skill_manifest(self.config.skills_dir)` and stores the result in `self._skill_manifest`.

**`_build_system_prompt()`** — new private method. Joins `self._identity`, `self._skill_manifest`, and the current timestamp. Replaces the inline concat currently on line 148.

**`handle()`** — before `self.sessions.setdefault(session_id, [])`, check `if session_id not in self.sessions` and call `self._refresh_manifest()` if true.

### Files unchanged

- `src/skills.py` — `build_skill_manifest()` and `load_skill()` stay as-is.
- `src/agent/system_prompt.py` — `build_static_prompt()` stays as-is, used only at init time.
- `src/config.py` — no changes.

## Testing

1. **New session picks up new skills:** Create an `Agent`, call `handle()` with session "s1", add a new skill dir to the tmp skills folder, call `handle()` with session "s2" — assert the new skill name appears in the system prompt sent to the LLM.
2. **Same session does not re-scan:** Call `handle()` twice with the same session ID — assert `build_skill_manifest` is NOT called on the second invocation.
