# Portal Conversation Starters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show example conversation starters on the empty portal chat screen — a static greeting plus one starter per portal-enabled skill — so a new user can act on what Curunir does in one click.

**Architecture:** All changes land in the single static file `portal/static/index.html`. A new `renderEmptyState()` builds a greeting + skill-derived starter list into `#messages` whenever a conversation is empty; a new `fillComposer()` helper drops `/<skill> ` into the composer on click (it does not send). Lifecycle hooks render the empty state on an empty `history_snapshot` and on **New**, remove it when the first message appears, and refresh it when a late `skills_snapshot` arrives. The skill list is fetched eagerly on connect.

**Tech Stack:** Plain browser JavaScript + CSS in one static HTML file. No build step, no framework, no JavaScript test harness — `portal/static/index.html` is served verbatim. Because there is no JS test framework, each task here is *implement → commit*, and **Task 4 is the manual verification gate** that must pass before the work is considered done.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-16-portal-conversation-starters-design.md`.

---

### Task 1: Add empty-state CSS

**Files:**
- Modify: `portal/static/index.html` (insert after the `.system-msg` rule, currently line 194)

- [ ] **Step 1: Add the CSS block**

In `portal/static/index.html`, find this line inside the `<style>` block:

```css
.system-msg { color: var(--system-text); }
```

Insert the following immediately **after** that line (keep the existing line in place):

```css

/* === Empty-state conversation starters === */
/* Shown inside #messages when a conversation has no messages yet: a static
   greeting plus one clickable starter per portal-enabled skill. */
.empty-state {
  height: 100%;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.es-greeting {
  font-size: 18px; font-weight: 600; color: var(--fg);
  margin-bottom: 22px;
}
.es-list {
  display: flex; flex-direction: column; gap: 2px;
  width: 100%; max-width: 400px;
}
.es-starter {
  display: flex; align-items: baseline; gap: 10px;
  padding: 9px 12px; border-radius: 8px; cursor: pointer;
  transition: background .12s;
}
.es-starter:hover { background: var(--hover-bg); }
.es-arrow { color: var(--hint); flex: none; font-size: 13px; }
.es-text { font-size: 13.5px; color: var(--fg); line-height: 1.4; }
.es-starter:hover .es-text,
.es-starter:hover .es-arrow { color: var(--accent); }
```

These selectors reuse existing theme variables (`--fg`, `--hint`, `--accent`, `--hover-bg`) so they work in both light and dark themes with no extra rules.

- [ ] **Step 2: Verify the file still parses**

Open `portal/static/index.html` in a browser directly (e.g. `open portal/static/index.html` on macOS). The page will not connect to a WebSocket, but it must render without a blank white screen or a console syntax error. Confirm the browser devtools console shows no CSS or HTML parse errors.

Expected: page loads, header "Curunir" visible, no console errors.

- [ ] **Step 3: Commit**

```bash
git add portal/static/index.html
git commit -m "feat(portal): add empty-state conversation starter styles"
```

---

### Task 2: Add `renderEmptyState()` and `fillComposer()`

**Files:**
- Modify: `portal/static/index.html` (insert after `scrollToBottom()`, before the `// === Skills picker ===` comment, currently line 834-836)

- [ ] **Step 1: Add the two functions**

In `portal/static/index.html`, find this block (the end of `scrollToBottom` followed by the Skills picker section header):

```javascript
function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

// === Skills picker ===
```

Insert the following **between** the closing `}` of `scrollToBottom` and the `// === Skills picker ===` comment:

```javascript

// === Empty-state conversation starters ===
// Rendered into #messages when a conversation has no messages yet: a static
// greeting plus one starter per portal-enabled skill. Rebuilt in place on
// every call, so a late skills_snapshot can refresh the rows. A starter
// click drops `/<skill> ` into the composer via fillComposer — it does NOT
// send; the skills all need a subject the user must still type.
function renderEmptyState() {
  const existing = messagesEl.querySelector(".empty-state");
  if (existing) existing.remove();

  const wrap = document.createElement("div");
  wrap.className = "empty-state";

  const greeting = document.createElement("div");
  greeting.className = "es-greeting";
  greeting.textContent = "What would you like to do?";
  wrap.appendChild(greeting);

  const skills = skillsCache || [];
  if (skills.length) {
    const list = document.createElement("div");
    list.className = "es-list";
    for (const s of skills) {
      const row = document.createElement("div");
      row.className = "es-starter";
      row.innerHTML =
        '<span class="es-arrow">→</span>' +
        '<span class="es-text"></span>';
      row.querySelector(".es-text").textContent = s.summary;
      row.addEventListener("click", () => fillComposer("/" + s.name + " "));
      list.appendChild(row);
    }
    wrap.appendChild(list);
  }
  messagesEl.appendChild(wrap);
}

// Drop text into the composer, focus it, and place the caret at the end.
// Fires the existing `input` handler so the textarea autosizes. Used by the
// empty-state starters; deliberately does not send.
function fillComposer(text) {
  inputEl.value = text;
  inputEl.focus();
  inputEl.setSelectionRange(text.length, text.length);
  inputEl.dispatchEvent(new Event("input"));
}
```

Notes for the implementer:
- `messagesEl`, `skillsCache`, and `inputEl` are existing module-level
  globals declared near the top of the `<script>` block — do not redeclare
  them.
- `s.summary` and `s.name` are fields of each entry in the skills snapshot
  (see `portal_skill_list()` in `src/skills.py`); `s.summary` is the
  curated `portal_summary` phrase, `s.name` is the slug used in `/<name>`.
- `renderEmptyState` references `fillComposer`; both are function
  declarations, so definition order within this block does not matter.

- [ ] **Step 2: Verify the file still parses**

Reload `portal/static/index.html` in the browser. Open devtools console.

Expected: no JavaScript syntax errors. (`renderEmptyState` is defined but
not yet called — that wiring is Task 3.)

- [ ] **Step 3: Commit**

```bash
git add portal/static/index.html
git commit -m "feat(portal): add renderEmptyState and fillComposer helpers"
```

---

### Task 3: Wire lifecycle hooks and eager skill fetch

This task makes five small edits to `portal/static/index.html` so the empty
state actually appears, refreshes, and clears. Make all five edits, then
commit once.

**Files:**
- Modify: `portal/static/index.html` (five separate locations)

- [ ] **Step 1: Fetch the skill list eagerly on connect**

Find this block inside `ws.onopen` (currently lines 624-629):

```javascript
    ws.send(JSON.stringify({
      content: "",
      command: "history_request",
      session_id: sessionId,
    }));
  };
```

Replace it with:

```javascript
    ws.send(JSON.stringify({
      content: "",
      command: "history_request",
      session_id: sessionId,
    }));
    // Fetch the skill list now so the empty-state starters are ready on
    // first paint (the skills picker also requests this lazily).
    ws.send(JSON.stringify({
      command: "skills_request", session_id: sessionId,
    }));
  };
```

- [ ] **Step 2: Render the empty state on an empty history snapshot**

Find this block in `onServerMessage` (currently lines 645-651):

```javascript
  if (msg.type === "history_snapshot") {
    messagesEl.innerHTML = "";
    inProgressMsg = null;
    for (const m of msg.messages) renderHistoryEntry(m);
    updateComposerMode();
    return;
  }
```

Replace it with:

```javascript
  if (msg.type === "history_snapshot") {
    messagesEl.innerHTML = "";
    inProgressMsg = null;
    for (const m of msg.messages) renderHistoryEntry(m);
    if (!msg.messages.length) renderEmptyState();
    updateComposerMode();
    return;
  }
```

- [ ] **Step 3: Refresh the empty state when a late skill snapshot arrives**

Find this block in `onServerMessage` (currently lines 652-658):

```javascript
  if (msg.type === "skills_snapshot") {
    skillsCache = msg.skills || [];
    if (!skillsOverlay.hidden) {
      renderSkillsList(skillsCache, skillsSearch.value);
    }
    return;
  }
```

Replace it with:

```javascript
  if (msg.type === "skills_snapshot") {
    skillsCache = msg.skills || [];
    if (!skillsOverlay.hidden) {
      renderSkillsList(skillsCache, skillsSearch.value);
    }
    // If the empty state is showing, it may have rendered before skills
    // arrived — rebuild it now so the starter rows appear.
    if (messagesEl.querySelector(".empty-state")) renderEmptyState();
    return;
  }
```

- [ ] **Step 4: Clear the empty state when the first message appears**

Find the start of `appendMessage` (currently lines 812-814):

```javascript
function appendMessage(role, thinking = false) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
```

Replace it with:

```javascript
function appendMessage(role, thinking = false) {
  const es = messagesEl.querySelector(".empty-state");
  if (es) es.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
```

- [ ] **Step 5: Re-render the empty state on New**

Find this block at the end of `startNew` (currently lines 1064-1067):

```javascript
  renderStaged();
  inputEl.value = "";
  inputEl.style.height = "";
  inputEl.focus();
}
```

Replace it with:

```javascript
  renderStaged();
  inputEl.value = "";
  inputEl.style.height = "";
  renderEmptyState();
  inputEl.focus();
}
```

- [ ] **Step 6: Verify the file still parses**

Reload `portal/static/index.html` in the browser. Open devtools console.

Expected: no JavaScript syntax errors. (Full behavior is checked in Task 4
against a running portal.)

- [ ] **Step 7: Commit**

```bash
git add portal/static/index.html
git commit -m "feat(portal): show conversation starters on the empty chat screen"
```

---

### Task 4: Manual verification gate

There is no JavaScript test harness for the portal, so this task is the
acceptance gate. Run the portal locally with a connected agent and walk the
checklist. If any step fails, fix `portal/static/index.html` and re-run the
whole checklist.

**Files:**
- No code changes unless a checklist step fails.

- [ ] **Step 1: Start the portal locally**

Follow the "Local development" section of `portal/README.md` (the
containerized path: `docker compose up` from the repo root brings up
Postgres and the portal). Then start a curunir container/agent so it dials
the portal and reports **online**. Open the portal URL in a browser and
sign in.

- [ ] **Step 2: Verify the empty state renders**

On a fresh browser tab (new session), once the agent shows **online**:

Expected: `#messages` shows the centered greeting "What would you like to
do?" and, below it, one `→`-prefixed starter row per portal-enabled skill
(4 today: investment-memo, deep-research, financial-analysis,
fact-checker), each showing that skill's `portal_summary` text. No emoji.

- [ ] **Step 3: Verify a starter fills the composer without sending**

Click any starter row.

Expected: the composer input fills with `/<skill-name> ` (trailing space),
the textarea is focused with the caret at the end, the textarea has
autosized, and **no message is sent** — `#messages` still shows the empty
state, the agent is idle.

- [ ] **Step 4: Verify the empty state clears on the first message**

Type a subject after the pre-filled `/<skill> ` and press Enter (or type
any plain message and send).

Expected: the empty state disappears the moment the user message is
appended; the conversation renders normally.

- [ ] **Step 5: Verify New brings the empty state back**

Click the **New** button in the header.

Expected: `#messages` clears and the empty state (greeting + starters)
renders again.

- [ ] **Step 6: Verify a reloaded conversation does NOT show the empty state**

With a conversation that has at least one message, reload the browser tab.

Expected: the prior messages render from the `history_snapshot`; the empty
state does **not** appear.

- [ ] **Step 7: Verify dark mode**

Toggle the theme button and repeat Step 2 visually.

Expected: greeting and starter text are legible; starter hover background
and accent color render correctly in dark mode.

- [ ] **Step 8: Record the result**

If every step passed, the feature is complete — no further commit needed
(all code was committed in Tasks 1-3). If a step failed, fix
`portal/static/index.html`, commit the fix with a `fix(portal):` message,
and re-run this checklist from Step 2.
