# Portal Skills Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give portal users a tappable, mobile-friendly ⚡ picker to discover and launch the skills meant for them.

**Architecture:** Three layers. (1) `src/skills.py` gains optional `portal_summary` / `portal_icon` frontmatter fields and a `portal_skill_list` helper. (2) `PortalChannel` answers a new `skills_request` frame with a `skills_snapshot`, mirroring the existing `history_request`/`history_snapshot` round-trip and routed by `session_id`. (3) `portal/static/index.html` gets a ⚡ button opening a responsive bottom-sheet/popover picker; tapping a skill reuses the existing slash-command path.

**Tech Stack:** Python 3.12 (asyncio), pytest / pytest-asyncio, `websockets`, FastAPI (portal), vanilla JS/CSS (portal frontend).

**Design spec:** `docs/superpowers/specs/2026-05-15-portal-skills-picker-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/skills.py` | `Skill` dataclass, registry, `portal_skill_list` helper | Modify |
| `tests/test_skills.py` | Skill registry tests | Modify |
| `src/channels/portal.py` | PortalChannel — `skills_request` handling | Modify |
| `tests/test_portal_channel.py` | PortalChannel tests | Modify |
| `run.py` | Wire `skills_provider` into `PortalChannel(...)` | Modify |
| `portal/ws_agent.py` | Route `skills_snapshot` to the browser by session | Modify |
| `portal/tests/test_ws_agent.py` | Agent-side routing tests | Modify |
| `skills/investment-memo/SKILL.md` etc. | Opt 4 skills into the picker | Modify |
| `portal/static/index.html` | ⚡ button + picker component | Modify |

`portal/ws_browser.py` needs **no change** — it pipes whatever `route_to_session` delivers straight to the browser socket, and the browser already dispatches by `msg.type`.

---

## Task 1: Skill metadata fields + `portal_skill_list`

**Files:**
- Modify: `src/skills.py`
- Test: `tests/test_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_skills.py`:

```python
from src.skills import portal_skill_list


def _write_portal_skill(parent, name, description, summary=None, icon=None):
    """Create skills/<name>/SKILL.md with optional portal_* frontmatter."""
    d = parent / name
    d.mkdir()
    lines = [f"name: {name}", f"description: {description}"]
    if summary is not None:
        lines.append(f'portal_summary: "{summary}"')
    if icon is not None:
        lines.append(f'portal_icon: "{icon}"')
    (d / "SKILL.md").write_text(
        "---\n" + "\n".join(lines) + f"\n---\n# {name}\n"
    )
    return d / "SKILL.md"


class TestPortalMetadata:
    def test_portal_fields_parsed_into_skill(self, tmp_path):
        _write_portal_skill(tmp_path, "memo", "agent desc",
                            summary="User-facing line", icon="📊")
        skill = load_registry([tmp_path])["memo"]
        assert skill.portal_summary == "User-facing line"
        assert skill.portal_icon == "📊"

    def test_portal_fields_default_none(self, tmp_path):
        _write_skill(tmp_path, "plain", "agent desc")
        skill = load_registry([tmp_path])["plain"]
        assert skill.portal_summary is None
        assert skill.portal_icon is None


class TestPortalSkillList:
    def test_only_skills_with_summary_appear(self, tmp_path):
        _write_portal_skill(tmp_path, "shown", "d", summary="visible")
        _write_skill(tmp_path, "hidden", "d")
        result = portal_skill_list([tmp_path])
        names = [s["name"] for s in result]
        assert names == ["shown"]

    def test_blank_summary_excluded(self, tmp_path):
        _write_portal_skill(tmp_path, "blank", "d", summary="")
        assert portal_skill_list([tmp_path]) == []

    def test_disabled_skill_excluded(self, tmp_path):
        d = tmp_path / "off"
        d.mkdir()
        (d / "SKILL.md").write_text(
            '---\nname: off\ndescription: d\n'
            'portal_summary: "x"\ndisabled: true\n---\n'
        )
        assert portal_skill_list([tmp_path]) == []

    def test_display_name_derived_from_name(self, tmp_path):
        _write_portal_skill(tmp_path, "investment-memo", "d", summary="s")
        assert portal_skill_list([tmp_path])[0]["display_name"] == "Investment memo"

    def test_icon_defaults_to_lightning(self, tmp_path):
        _write_portal_skill(tmp_path, "noicon", "d", summary="s")
        assert portal_skill_list([tmp_path])[0]["icon"] == "⚡"

    def test_entries_sorted_by_name(self, tmp_path):
        _write_portal_skill(tmp_path, "zeta", "d", summary="s")
        _write_portal_skill(tmp_path, "alpha", "d", summary="s")
        assert [s["name"] for s in portal_skill_list([tmp_path])] == ["alpha", "zeta"]

    def test_entry_shape(self, tmp_path):
        _write_portal_skill(tmp_path, "memo", "d", summary="A memo", icon="📊")
        assert portal_skill_list([tmp_path]) == [
            {"name": "memo", "display_name": "Memo",
             "summary": "A memo", "icon": "📊"}
        ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skills.py -k "Portal" -v`
Expected: FAIL — `ImportError: cannot import name 'portal_skill_list'`.

- [ ] **Step 3: Add the dataclass fields**

In `src/skills.py`, replace the `Skill` dataclass:

```python
@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    portal_summary: str | None = None
    portal_icon: str | None = None
```

- [ ] **Step 4: Populate the fields in `load_registry`**

In `src/skills.py`, replace the `registry[name] = Skill(...)` construction inside `load_registry` with:

```python
            registry[name] = Skill(
                name=name,
                description=fm["description"],
                path=skill_file,
                portal_summary=fm.get("portal_summary") or None,
                portal_icon=fm.get("portal_icon") or None,
            )
```

- [ ] **Step 5: Add `portal_skill_list` and `_display_name`**

In `src/skills.py`, add after `build_skill_manifest`:

```python
def _display_name(name: str) -> str:
    """Derive a user-facing label: 'investment-memo' -> 'Investment memo'."""
    words = name.replace("-", " ").replace("_", " ")
    return words[:1].upper() + words[1:]


def portal_skill_list(skill_dirs: list[Path]) -> list[dict]:
    """User-facing skills for the portal picker.

    Returns only skills that opted in with a non-empty `portal_summary`,
    sorted by name. Each entry: {name, display_name, summary, icon}.
    """
    registry = load_registry(skill_dirs)
    out = []
    for skill in registry.values():
        if not skill.portal_summary:
            continue
        out.append({
            "name": skill.name,
            "display_name": _display_name(skill.name),
            "summary": skill.portal_summary,
            "icon": skill.portal_icon or "⚡",
        })
    out.sort(key=lambda s: s["name"])
    return out
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_skills.py -v`
Expected: PASS — all tests, including the pre-existing ones (the new dataclass fields have defaults, so existing `Skill(...)` callers are unaffected).

- [ ] **Step 7: Commit**

```bash
git add src/skills.py tests/test_skills.py
git commit -m "feat: add portal_summary/portal_icon skill metadata and portal_skill_list"
```

---

## Task 2: PortalChannel `skills_request` handling

**Files:**
- Modify: `src/channels/portal.py`
- Test: `tests/test_portal_channel.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_portal_channel.py`:

```python
@pytest.mark.asyncio
async def test_skills_request_invokes_provider_and_sends_snapshot(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    fake_skills = [
        {"name": "memo", "display_name": "Memo",
         "summary": "A memo", "icon": "📊"},
    ]

    def provider() -> list[dict]:
        return fake_skills

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        skills_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "skills_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "skills_snapshot"
        assert msg["skills"] == fake_skills
        assert msg["session_id"] == PORTAL_SESSION_ID
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_skills_request_command_triggers_snapshot(portal_server):
    """Browser-driven: a user_message with command=skills_request and a
    session_id triggers a skills_snapshot tagged with that session."""
    in_q: asyncio.Queue = asyncio.Queue()

    def provider() -> list[dict]:
        return []

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        skills_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "skills_request", "session_id": "tab-S"},
        })
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "skills_snapshot"
        assert msg["session_id"] == "tab-S"
        assert msg["skills"] == []
    finally:
        task.cancel()
```

Note: match the existing file's test style — if the other async tests in this
file are decorated with `@pytest.mark.asyncio`, keep the decorator; if the
file uses `asyncio_mode = auto`, drop it. Check the top of the file / the
existing `test_history_request_*` tests and mirror them exactly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portal_channel.py -k skills -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'skills_provider'`.

- [ ] **Step 3: Add the `skills_provider` constructor argument**

In `src/channels/portal.py`, in `PortalChannel.__init__`, add the parameter
after `history_provider`:

```python
        history_provider: "callable[[str], list[dict]] | None" = None,
        skills_provider: "callable[[], list[dict]] | None" = None,
        uploads_dir: str | None = None,
```

And in the body, after `self.history_provider = ...`:

```python
        self.skills_provider = skills_provider or (lambda: [])
```

- [ ] **Step 4: Add the `_handle_skills_request` method**

In `src/channels/portal.py`, add directly after `_handle_history_request`:

```python
    async def _handle_skills_request(self, payload: dict) -> None:
        if self._connection is None:
            return
        session_id = payload.get("session_id") or PORTAL_SESSION_ID
        skills = self.skills_provider()
        try:
            await self._connection.send(json.dumps({
                "type": "skills_snapshot",
                "session_id": session_id,
                "skills": skills,
            }))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal closed during skills snapshot send")
```

- [ ] **Step 5: Route the request in `_read_loop`**

In `src/channels/portal.py`, in `_read_loop`, add a branch after the
`history_request` branch:

```python
            elif mtype == "history_request":
                await self._handle_history_request(msg.get("payload") or {})
            elif mtype == "skills_request":
                await self._handle_skills_request(msg.get("payload") or {})
            else:
```

- [ ] **Step 6: Route the command in `_handle_user_message`**

In `src/channels/portal.py`, in `_handle_user_message`, add after the
`command == "history_request"` block:

```python
        if payload.get("command") == "skills_request":
            await self._handle_skills_request({"session_id": session_id})
            return
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_portal_channel.py -k skills -v`
Expected: PASS — both new tests.

- [ ] **Step 8: Run the full PortalChannel suite for regressions**

Run: `pytest tests/test_portal_channel.py -v`
Expected: PASS — all tests.

- [ ] **Step 9: Commit**

```bash
git add src/channels/portal.py tests/test_portal_channel.py
git commit -m "feat: PortalChannel answers skills_request with skills_snapshot"
```

---

## Task 3: Wire `skills_provider` in `run.py`

**Files:**
- Modify: `run.py` (the `PortalChannel(...)` construction, ~line 573)

This wiring has no unit test (it is glue inside `run.py`'s startup); it is
verified by Task 6's manual end-to-end check.

- [ ] **Step 1: Import `portal_skill_list`**

In `run.py`, find the existing import from `src.skills` (search for
`from src.skills import` or `import src.skills`). If a `from src.skills import ...`
line exists, add `portal_skill_list` to it. If skills are imported another
way, add a new line near the other `src.` imports:

```python
from src.skills import portal_skill_list
```

- [ ] **Step 2: Pass `skills_provider` to `PortalChannel`**

In `run.py`, in the `PortalChannel(...)` construction, add the
`skills_provider` argument after `history_provider`:

```python
        portal_channel = PortalChannel(
            in_queue=in_queue,
            url=portal_url,
            token=portal_token,
            history_provider=lambda sid: agent.history_snapshot(sid),
            skills_provider=lambda: portal_skill_list(agent.config.skill_dirs),
            cancel_session=agent.request_cancel,
        )
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "import run"`
Expected: no `ImportError` / `NameError` (it may print logging output and
exit cleanly — that is fine; we only care that import succeeds).

- [ ] **Step 4: Commit**

```bash
git add run.py
git commit -m "feat: wire portal skills_provider into PortalChannel"
```

---

## Task 4: Portal — route `skills_snapshot` to the browser

**Files:**
- Modify: `portal/ws_agent.py`
- Test: `portal/tests/test_ws_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `portal/tests/test_ws_agent.py`:

```python
def test_skills_snapshot_routes_by_session_id(sync_client, monkeypatch):
    user = _create_user(sync_client, "skillsnap@example.com")
    captured = []

    async def fake_route(user_id, session_id, payload):
        captured.append((user_id, session_id, payload))
        return 1

    monkeypatch.setattr(routing, "route_to_session", fake_route)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        snapshot = {
            "type": "skills_snapshot",
            "session_id": "tab-K",
            "skills": [{"name": "memo", "display_name": "Memo",
                        "summary": "A memo", "icon": "📊"}],
        }
        ws.send_text(json.dumps(snapshot))
        ws.close()

    targets = [(sid, json.loads(p)) for (_, sid, p) in captured]
    assert any(sid == "tab-K" and p == snapshot for sid, p in targets)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd portal && pytest tests/test_ws_agent.py::test_skills_snapshot_routes_by_session_id -v`
Expected: FAIL — the snapshot is not routed (`captured` stays empty; the
`else` branch logs "agent sent unknown type").

- [ ] **Step 3: Add the `skills_snapshot` branch**

In `portal/ws_agent.py`, in the agent-message read loop, add a branch
immediately after the existing `history_snapshot` branch:

```python
            elif mtype == "history_snapshot":
                session_id = msg.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    logger.warning(
                        "history_snapshot without session_id; dropping",
                        extra={"user_id": user.id},
                    )
                    continue
                await routing.route_to_session(user.id, session_id, raw)
            elif mtype == "skills_snapshot":
                session_id = msg.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    logger.warning(
                        "skills_snapshot without session_id; dropping",
                        extra={"user_id": user.id},
                    )
                    continue
                await routing.route_to_session(user.id, session_id, raw)
            else:
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd portal && pytest tests/test_ws_agent.py::test_skills_snapshot_routes_by_session_id -v`
Expected: PASS

- [ ] **Step 5: Run the full agent-WS suite for regressions**

Run: `cd portal && pytest tests/test_ws_agent.py -v`
Expected: PASS — all tests.

- [ ] **Step 6: Commit**

```bash
git add portal/ws_agent.py portal/tests/test_ws_agent.py
git commit -m "feat: portal routes skills_snapshot to browser by session_id"
```

---

## Task 5: Opt the four launch skills into the picker

**Files:**
- Modify: `skills/investment-memo/SKILL.md`
- Modify: `skills/financial-analysis/SKILL.md`
- Modify: `skills/deep-research/SKILL.md`
- Modify: `skills/fact-checker/SKILL.md`

Each SKILL.md has YAML frontmatter delimited by `---`. Add two lines inside
the frontmatter block (after the `description:` line, before the closing
`---`). Do **not** touch `description` or any other field.

- [ ] **Step 1: Edit `skills/investment-memo/SKILL.md`**

Add inside the frontmatter:

```yaml
portal_summary: "Fact-checked investment memo on any stock, sector, or asset"
portal_icon: "📊"
```

- [ ] **Step 2: Edit `skills/financial-analysis/SKILL.md`**

Add inside the frontmatter:

```yaml
portal_summary: "Valuation, scenarios, and peer comparison for a public company"
portal_icon: "💹"
```

- [ ] **Step 3: Edit `skills/deep-research/SKILL.md`**

Add inside the frontmatter:

```yaml
portal_summary: "Research any topic into a sourced written report"
portal_icon: "🔬"
```

- [ ] **Step 4: Edit `skills/fact-checker/SKILL.md`**

Add inside the frontmatter:

```yaml
portal_summary: "Independently verify the claims in a report or article"
portal_icon: "✅"
```

- [ ] **Step 5: Verify the picker list resolves to exactly these four**

Run:

```bash
python -c "from pathlib import Path; from src.skills import portal_skill_list; import json; print(json.dumps(portal_skill_list([Path('skills')]), indent=2, ensure_ascii=False))"
```

Expected: a JSON array of exactly four entries — `deep-research`,
`fact-checker`, `financial-analysis`, `investment-memo` (sorted by name) —
each with `display_name`, `summary`, and `icon` matching the values above.
No other skills appear.

- [ ] **Step 6: Commit**

```bash
git add skills/investment-memo/SKILL.md skills/financial-analysis/SKILL.md \
        skills/deep-research/SKILL.md skills/fact-checker/SKILL.md
git commit -m "feat: opt four launch skills into the portal picker"
```

---

## Task 6: Frontend — the ⚡ skills picker

**Files:**
- Modify: `portal/static/index.html`

The portal frontend has no JS test harness, so this task is verified
manually. Each step shows the exact code to insert and where.

- [ ] **Step 1: Add the picker CSS**

In `portal/static/index.html`, inside the `<style>` block, add the following
immediately before the closing `</style>` tag (after the
`@media (max-width: 480px)` rule):

```css
/* === Skills picker === */
#skills-btn {
  background: transparent; border: 0; color: var(--header-text);
  width: 32px; height: 32px; border-radius: 8px; cursor: pointer;
  font-size: 16px; padding: 6px; box-sizing: content-box;
  display: inline-flex; align-items: center; justify-content: center;
}
#skills-btn:hover { color: var(--accent); background: var(--hover-bg); }
#skills-overlay {
  position: fixed; inset: 0; z-index: 50;
  background: color-mix(in srgb, #000 38%, transparent);
  display: flex;
}
#skills-overlay[hidden] { display: none; }
#skills-panel {
  background: var(--bg); color: var(--fg);
  display: flex; flex-direction: column;
  border: 1px solid var(--border);
  margin: auto; width: 420px; max-width: 92vw; max-height: 70vh;
  border-radius: 14px; box-shadow: 0 12px 40px rgba(0,0,0,.3);
  overflow: hidden;
}
.skills-grab { display: none; }
.skills-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px 8px;
}
.skills-title { font-weight: 700; font-size: 15px; }
#skills-close {
  background: transparent; border: 0; color: var(--header-text);
  font-size: 15px; cursor: pointer; width: 28px; height: 28px;
  border-radius: 6px;
}
#skills-close:hover { background: var(--hover-bg); color: var(--accent); }
#skills-search {
  margin: 0 14px 8px; padding: 8px 10px;
  border: 1px solid var(--border-strong); border-radius: 8px;
  background: var(--bg); color: var(--fg);
  font-family: inherit; font-size: 13px;
}
#skills-search:focus { outline: 0; border-color: var(--accent); }
#skills-list { overflow-y: auto; padding: 0 8px; }
.skill-row {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 9px 8px; border-radius: 8px; cursor: pointer;
}
.skill-row:hover { background: var(--hover-bg); }
.skill-row .skill-icon { font-size: 20px; line-height: 1.1; flex: none; }
.skill-row .skill-name { font-weight: 600; font-size: 13px; }
.skill-row .skill-summary {
  color: var(--hint); font-size: 12px; line-height: 1.35;
}
.skills-empty {
  color: var(--hint); font-size: 13px; padding: 14px; text-align: center;
}
.skills-config-label {
  font-size: 10px; font-weight: 700; letter-spacing: .6px;
  color: var(--hint); border-top: 1px solid var(--border);
  margin: 6px 12px 0; padding: 8px 0 4px;
}
#skills-config { padding: 0 8px 10px; }
.skill-row.config { padding: 7px 8px; }
.skill-row.config .skill-icon { font-size: 15px; opacity: .8; }
.skill-row.config .skill-name {
  font-weight: 500; font-size: 12.5px; color: var(--header-text);
}
@media (max-width: 640px) {
  #skills-overlay { align-items: flex-end; }
  #skills-panel {
    margin: 0; width: 100%; max-width: 100%; max-height: 78vh;
    border-radius: 16px 16px 0 0; border-bottom: 0;
  }
  .skills-grab {
    display: block; width: 36px; height: 4px;
    background: var(--border-strong); border-radius: 2px;
    margin: 8px auto 2px;
  }
}
```

- [ ] **Step 2: Add the ⚡ button to the composer row**

In `portal/static/index.html`, in the `.composer-row` div, add the skills
button as the first child (before `#attach-btn`):

```html
    <div class="composer-row">
      <button id="skills-btn" title="Browse skills" aria-label="Browse skills">⚡</button>
      <button id="attach-btn" title="Attach files" aria-label="Attach files">📎</button>
```

- [ ] **Step 3: Add the picker markup**

In `portal/static/index.html`, add immediately after the closing `</footer>`
tag (before `<script>`):

```html
<div id="skills-overlay" hidden>
  <div id="skills-panel" role="dialog" aria-label="Skills" aria-modal="true">
    <div class="skills-grab"></div>
    <div class="skills-head">
      <span class="skills-title">⚡ Skills</span>
      <button id="skills-close" aria-label="Close">✕</button>
    </div>
    <input id="skills-search" type="text" placeholder="Search skills…"
           autocomplete="off">
    <div id="skills-list"></div>
    <div class="skills-config-label">SETTINGS</div>
    <div id="skills-config"></div>
  </div>
</div>
```

- [ ] **Step 4: Add element references and the static config list**

In `portal/static/index.html`, in the `// === Elements ===` section, add
after the existing `const themeBtn = ...` line:

```javascript
const skillsBtn = document.getElementById("skills-btn");
const skillsOverlay = document.getElementById("skills-overlay");
const skillsClose = document.getElementById("skills-close");
const skillsSearch = document.getElementById("skills-search");
const skillsListEl = document.getElementById("skills-list");
const skillsConfigEl = document.getElementById("skills-config");
```

In the `// === State ===` section, add:

```javascript
// Cached skills_snapshot for this tab; null until first fetch returns.
let skillsCache = null;
```

Add the static config footer list near the other top-level `const`
declarations (e.g. just after the `// === Constants ===` block):

```javascript
// Config footer — stable system commands, fixed (not skill-registry driven).
const SKILLS_CONFIG = [
  { icon: "🪪", label: "Identity", command: "/identity" },
  { icon: "👤", label: "Profile", command: "/profile" },
  { icon: "⚙️", label: "Preferences", command: "/preferences" },
  { icon: "❓", label: "Help", command: "/help" },
];
```

- [ ] **Step 5: Add the picker render/launch functions**

In `portal/static/index.html`, add this block in the `<script>` section
(e.g. just before the `// === Send ===` comment):

```javascript
// === Skills picker ===
function renderSkillsList(skills, filter = "") {
  if (!skills.length) {
    skillsListEl.innerHTML =
      '<div class="skills-empty">No skills available yet.</div>';
    return;
  }
  const f = filter.trim().toLowerCase();
  const shown = f
    ? skills.filter(s =>
        s.display_name.toLowerCase().includes(f) ||
        s.summary.toLowerCase().includes(f))
    : skills;
  if (!shown.length) {
    skillsListEl.innerHTML =
      '<div class="skills-empty">No matches.</div>';
    return;
  }
  skillsListEl.innerHTML = "";
  for (const s of shown) {
    const row = document.createElement("div");
    row.className = "skill-row";
    row.innerHTML =
      '<span class="skill-icon"></span>' +
      '<div><div class="skill-name"></div>' +
      '<div class="skill-summary"></div></div>';
    row.querySelector(".skill-icon").textContent = s.icon || "⚡";
    row.querySelector(".skill-name").textContent = s.display_name;
    row.querySelector(".skill-summary").textContent = s.summary;
    row.addEventListener("click", () => launchSkill("/" + s.name));
    skillsListEl.appendChild(row);
  }
}

function renderSkillsConfig() {
  skillsConfigEl.innerHTML = "";
  for (const c of SKILLS_CONFIG) {
    const row = document.createElement("div");
    row.className = "skill-row config";
    row.innerHTML =
      '<span class="skill-icon"></span><div class="skill-name"></div>';
    row.querySelector(".skill-icon").textContent = c.icon;
    row.querySelector(".skill-name").textContent = c.label;
    row.addEventListener("click", () => launchSkill(c.command));
    skillsConfigEl.appendChild(row);
  }
}

function openSkills() {
  skillsOverlay.hidden = false;
  skillsSearch.value = "";
  renderSkillsConfig();
  if (skillsCache) {
    renderSkillsList(skillsCache);
  } else {
    skillsListEl.innerHTML =
      '<div class="skills-empty">Loading…</div>';
    ws.send(JSON.stringify({
      command: "skills_request", session_id: sessionId,
    }));
  }
  skillsSearch.focus();
}

function closeSkills() {
  skillsOverlay.hidden = true;
}

// Launch a skill/command via the existing slash path: echo locally and
// send the same {command:"slash"} frame a typed slash would produce.
function launchSkill(slashText) {
  closeSkills();
  if (!agentOnline) {
    const el = appendMessage("assistant");
    el.querySelector(".body").innerHTML = "<em>Agent is offline.</em>";
    return;
  }
  const el = appendMessage("user");
  el.querySelector(".body").textContent = slashText;
  ws.send(JSON.stringify({
    command: "slash", text: slashText, session_id: sessionId,
  }));
  if (!inProgressMsg) inProgressMsg = appendMessage("assistant", true);
  updateComposerMode();
}

skillsBtn.addEventListener("click", openSkills);
skillsClose.addEventListener("click", closeSkills);
skillsOverlay.addEventListener("click", (e) => {
  if (e.target === skillsOverlay) closeSkills();
});
skillsSearch.addEventListener("input", () => {
  if (skillsCache) renderSkillsList(skillsCache, skillsSearch.value);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !skillsOverlay.hidden) closeSkills();
});
```

- [ ] **Step 6: Handle the `skills_snapshot` reply**

In `portal/static/index.html`, in the `onServerMessage` function, add this
branch after the `history_snapshot` branch (before the final
`renderAgentChunk(msg);` line):

```javascript
  if (msg.type === "skills_snapshot") {
    skillsCache = msg.skills || [];
    if (!skillsOverlay.hidden) {
      renderSkillsList(skillsCache, skillsSearch.value);
    }
    return;
  }
```

- [ ] **Step 7: Manual verification — desktop**

Start the stack and open the portal in a desktop browser
(see `CLAUDE.md` / `portal/README.md` for the run commands; e.g.
`docker compose up --build`, or run the portal and container locally).

Verify:
- The ⚡ button appears in the composer row, left of 📎.
- Clicking ⚡ opens a centered popover with a search box, a list of exactly
  four skills (Investment memo, Financial analysis, Deep research,
  Fact-checker) each with an icon + summary, a faint "SETTINGS" divider, and
  four config rows (Identity, Profile, Preferences, Help).
- Typing in the search box filters the four skills (and does **not** filter
  the config rows).
- Clicking a skill row closes the picker, echoes `/<name>` as a user message,
  and the agent begins responding.
- Clicking a config row (e.g. Help) sends that slash command.
- The picker closes on ✕, on clicking the dimmed backdrop, and on Escape.

- [ ] **Step 8: Manual verification — mobile**

In the browser devtools, switch to a mobile viewport (≤640px wide), reload.

Verify:
- The picker opens as a bottom sheet (anchored to the bottom edge, full
  width, rounded top corners) with a grab handle visible at the top.
- Search, skill rows, config footer, and tap-to-launch all behave as on
  desktop.

- [ ] **Step 9: Commit**

```bash
git add portal/static/index.html
git commit -m "feat: add tappable skills picker to the portal UI"
```

---

## Self-Review Notes

- **Spec coverage:** Layer 1 (metadata + `portal_skill_list`) → Task 1; Layer 2
  (`skills_request`/`skills_snapshot`, session routing) → Tasks 2–4; Layer 3
  (⚡ button, picker, search, config footer, tap-to-launch, dismissal) →
  Task 6; metadata rollout → Task 5. The "no opted-in skills" empty state,
  blank-`portal_summary` exclusion, and `portal_icon` default are all
  covered by Task 1 tests and Task 6 rendering.
- **Slash reuse:** `launchSkill` deliberately mirrors the existing slash
  branch of `send()` (echo + `{command:"slash"}` frame + `inProgressMsg` +
  `updateComposerMode()`), so tap-to-launch needs no backend invocation code.
- **Out of scope (unchanged):** `src/slash_commands.py` `/skills` text
  command, `cli.py`, `src/channels/ws.py`, `portal/ws_browser.py`.
