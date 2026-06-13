# Local UI / Portal Shared Chat Module — Design

**Date:** 2026-06-12
**Status:** Approved for implementation (Phase 1)
**Topic:** Extract the chat pane into a reusable module so the local web console reaches feature parity with the portal, and the two frontends stop drifting.

---

## 1. Problem

The local web console (`src/local_ui/static/index.html`, ~366 lines) and the portal
(`portal/static/index.html`, ~2088 lines) both implement a chat UI over the **same wire
protocol** (`user_message` / `agent_message` / `history_snapshot` / `skills_snapshot`),
but as two independent, hand-written frontends. They have drifted:

- Local is **dark-mode only** (hardcoded CSS variables); portal has a light/dark toggle.
- Local lacks the **tool ticker** (live "● Thinking + current tool" indicator).
- Local lacks **attachments** (attach button, drag/drop/paste, image previews, downloads).
- Local **discards `skills_snapshot`** (`onServerMessage` returns on it) — no skills panel.

The drift exists because the portal chat is not a component — it is woven into a 2088-line
monolith alongside portal-only machinery (multi-conversation sidebar, ephemeral scratchpad,
sign-in, per-`session_id` routing). Re-implementing the four missing features in the local
SPA would re-create the very duplication that caused the drift.

## 2. Goal & Non-Goals

**Goal:** Give the local console full chat parity with the portal (theme toggle, tool ticker,
attachments, skills panel) by extracting the chat pane into a **reusable ES module** whose
interface is shaped so the portal can later adopt it without redesign.

**Non-Goals (Phase 1):**
- Do **not** touch `portal/` — it is a production Render deployment. Portal adoption is Phase 2.
- No backend changes — the local channel already supports every needed frame (see §6).
- No multi-conversation or scratchpad in the local console — it is single-user, single-session
  by design (`session_id = "local"`).
- No build step / bundler — browsers run ES modules natively.

## 3. Decision

**Approach B (shared module), phased, local-first.** Build the canonical reusable module
**inside `src/local_ui/static/`**, wire it to the local console only, and leave the portal
untouched. The module's *interface* is designed to satisfy the portal too, so Phase 2 is
"delete portal's inline chat code, pass different hooks" — deletion, not another extraction.

**Why phased / why the module can't just be imported across both:** the portal and the local
UI are **separate deploy units that do not share a filesystem at build time**:

- Portal: `render.yaml` → `rootDir: portal`; Dockerfile → `COPY . ./portal/`. Build context is
  `portal/` only — it cannot reach `../src/`.
- Local UI: served by `local_web.py` `StaticFiles` from `src/local_ui/static/`, in the main
  container.

A truly shared file would therefore need a **canonical-source + synced-copy** mechanism
(copy script + CI drift-check). Rather than design that now, Phase 1 keeps the canonical in
`src/local_ui/static/` and **defers the sync-mechanism decision to Phase 2**, when the portal
actually needs a copy. (YAGNI: don't build the sync rig until there's a second consumer.)

**Accepted tradeoff:** during Phase 1 the chat logic exists twice (portal's inline copy + the
new module). This is temporary and intentional; the module boundary is correct from day one.

## 4. Architecture

```
src/local_ui/static/                         (canonical home for Phase 1)
  ├─ connection.js   socket lifecycle: connect, reconnect/backoff, status events,
  │                  send(frame), subscribe(handler). Knows nothing about chat.
  ├─ chat.js         chat-pane factory: createChat(config). Owns ALL chat DOM
  │                  (renders into a host-provided container), message streaming,
  │                  tool ticker, attachments, composer (send/stop), skills picker,
  │                  theme toggle, markdown. Consumes a connection. Emits onTurnFinal.
  ├─ chat.css        chat-pane styles + theme tokens (:root / [data-theme]).
  │                  Theme tokens are shared so the read-panels theme with the chat.
  └─ index.html      host "chrome": read-panel tabs (Usage / Balance Sheet / Memory /
                     Schedules, unchanged) + <div id="chat-root"> + bootstrap glue.

portal/  ........... UNTOUCHED in Phase 1.
```

**Node/edge view** (reusable nodes, explicit data flow):

```
   index.html (host)
        │ creates
        ▼
   connection.js ──subscribe(frame)──► chat.js ──renders──► #chat-root DOM
        ▲                                  │
        └──────────── send(frame) ─────────┘
   (host also wires: read-panel tabs ── REST ──► /api/usage, /api/portfolio, …)
```

- `connection.js` is a pure transport node — reusable by chat, and (Phase 2) by the portal
  sidebar/scratchpad on the same socket.
- `chat.js` is a self-contained chat node — give it a container + a connection + config, it
  runs. No global state, no references to portal-only or local-only chrome.
- `chat.css` carries the theme tokens so both the chat and the host's read-panels restyle
  together when the theme toggles.

## 5. Module Interfaces

### 5.1 `connection.js`

```js
// createConnection({ url, onStatus, onFrame }) -> { send, close }
//   url:      () => string   builds the ws URL (local injects ?token=…; portal injects none)
//   onStatus: (state) => {}  "reconnecting" | "online" | "offline"
//   onFrame:  (msg)   => {}   every parsed inbound frame (chat filters by session itself)
// Owns: WebSocket, exponential backoff (1s → 30s cap), auto-reconnect, JSON parse.
```

Lifted from the reconnect/backoff logic shared verbatim between both current files
(`local index.html:335-345`, `portal index.html:1047-1070`).

### 5.2 `chat.js`

```js
// createChat({
//   container,                 // DOM node to render the whole chat pane into (#chat-root)
//   connection,                // a connection.js instance
//   getSessionId,              // () => string   local: () => "local"; portal: () => liveUUID
//   features: {                // which capabilities to mount
//     attachments: true,
//     skills:      true,
//     theme:       true,
//     emptyState:  true,       // greeting + skill starters
//   },
//   hooks: {                   // portal-only seams; local passes no-ops / omits
//     onUnhandledFrame(msg) {} // frames chat doesn't own (conversations_snapshot, scratch)
//     onTurnFinal() {}         // portal refreshes its sidebar; local no-op
//     onSocketOpen() {}        // portal sends scratch_discard; local no-op
//   },
// }) -> { bootstrap(), rebind(sessionId), destroy() }
```

**Ownership:** `chat.js` renders its own DOM subtree (messages list, composer, attach controls,
skills modal, offline overlay) into `container` from a template string. The host HTML only
provides `<div id="chat-root">`. This is what removes the element-ID coupling between the two
host files and prevents future drift.

**Frame routing** (replaces the monolith's branchy `onServerMessage`): chat handles
`agent_status`, `history_snapshot`, `skills_snapshot`, and streaming `agent_message` chunks
whose `session_id` matches `getSessionId()`. Anything else is passed to
`hooks.onUnhandledFrame(msg)` (portal routes scratch/conversations; local ignores).

**Sends** all go through `connection.send(frame)` with `session_id` from `getSessionId()` —
no hardcoded session anywhere in the module. Frame shapes are unchanged from today:
`{content, session_id, attachments?}`, `{command:"slash", text, session_id}`,
`{command:"interrupt", session_id}`, `{command:"history_request"|"skills_request", session_id}`.

### 5.3 `chat.css`

All `.msg`, composer, attachment-card, tool-ticker, skills-modal, offline-overlay styles, plus
the `:root` / `[data-theme="dark"]` token blocks. The host's read-panel styles stay in
`index.html` but reference the same `--bg`/`--fg`/`--border`/… tokens, so toggling the theme
restyles the whole console.

## 6. Server Side — No Changes Needed

`local_web.py`'s `/ws/browser` bridge **already supports every Phase-1 feature** (verified):

| Feature        | Server support in `local_web.py`                                  |
|----------------|-------------------------------------------------------------------|
| Tool ticker    | streams `agent_message` chunks (existing outbound path, L307-315) |
| Attachments    | `_decode_attachments` / `_stage_attachments` / `_enrich_attachments` (L36-39, 268-281, 307) |
| Skills panel   | `skills_request` → `skills_snapshot` (L249-252)                    |
| History        | `history_request` → enriched `history_snapshot` (L236-241)         |
| Interrupt      | `interrupt` command (L227)                                         |
| Slash          | `slash` command (L258)                                             |

Phase 1 is therefore **frontend-only**: the local UI gains features the server already serves.

## 7. Feature Port List (lift from portal as reference)

Behavior is matched by lifting the portal's implementations (cited for the implementer):

1. **Theme toggle** — `applyTheme`/`toggleTheme` + dual hljs `<link disabled>` swap
   (`portal:937-951`); persist to `localStorage["curunir-theme"]`.
2. **Tool ticker** — `ensureActivityIndicator` / `appendToolCalls` / `setCurrentTool` /
   `finalizeActivity` / `toStatusLabel` + `TOOL_GERUNDS` (`portal:1179-1271`).
3. **Attachments** — staging (`stageFile`, `bytesToBase64`, `renderStaged`, size/MIME consts
   `portal:851-858`), rendering (`renderAttachments`, `downloadAttachment`), and the
   drag/drop/paste handlers (`portal:2038-2080`).
4. **Skills panel** — `renderSkillsList` / `renderSkillsConfig` / `openSkills` / `closeSkills`
   / `launchSkill` + the search box (`portal:1524-1603`). `SKILLS_CONFIG` (`portal:861-866`).

Also lifted as core (not on the "missing" list but needed by the module): `setUserBody`,
`renderHistoryEntry`, `renderAgentChunk`, `appendMessage`, `appendResponseActions` (Copy/Print),
markdown setup, composer autosize/collapse, offline modal, send/stop composer mode.

## 8. Host (`index.html`) Rewrite

- **Keep unchanged:** the read-panel tabs and their loaders (`loadUsage`, `loadPortfolio`,
  `loadMemory`, `loadSchedules`) and the `apiGet`/token plumbing (`local:136-270`).
- **Replace:** the inline chat block (`local:272-363`) with:
  ```html
  <div class="tab" id="tab-chat"><div id="chat-root"></div></div>
  <script type="module">
    import { createConnection } from "./connection.js";
    import { createChat } from "./chat.js";
    const TOKEN = new URLSearchParams(location.search).get("token") || "";
    const conn = createConnection({ url: () => wsUrlWithToken(TOKEN), onStatus, onFrame });
    const chat = createChat({
      container: document.getElementById("chat-root"),
      connection: conn,
      getSessionId: () => "local",
      features: { attachments: true, skills: true, theme: true, emptyState: true },
      hooks: {},                          // no portal chrome locally
    });
    chat.bootstrap();
  </script>
  ```
- **Link** `chat.css`; move the shared theme tokens out of the inline `<style>` into it.
- The tab-switch glue stays; chat lives in the `#tab-chat` pane as today.

## 9. Error Handling

- **Socket loss:** `connection.js` owns reconnect/backoff (1s→30s) and emits `offline` →
  chat shows the offline overlay and disables the composer (lift portal's offline modal).
- **Oversized / wrong-type attachment:** `stageFile` rejects client-side against the size/MIME
  constants before encoding; show an inline error, don't stage. (Server also re-validates.)
- **Bad frame / parse error:** `connection.js` wraps `JSON.parse` in try/catch and drops
  malformed frames rather than throwing in `onmessage`.
- **Token missing/expired:** REST `apiGet` already surfaces the error in the panel; the ws will
  fail the handshake and `connection.js` will retry — the offline overlay communicates this.

## 10. Forward-Compatibility (Phase 2, not built now)

The interface is sized for the portal so adoption is deletion + rewiring:
- `getSessionId` → portal passes its live UUID getter; `rebind(id)` supports conversation switch.
- `hooks.onUnhandledFrame` → portal routes `conversations_snapshot` and scratch frames.
- `hooks.onTurnFinal` → portal refreshes the sidebar; `hooks.onSocketOpen` → scratch discard.
- The scratchpad can later be a **second `createChat` instance** (own container, `session="scratch"`,
  `features:{attachments:false, skills:false}`) sharing the one connection — deduping portal's
  scratch code too. Out of scope now; the interface allows it.
- **Deferred decision:** the canonical-source sync mechanism (copy script + CI drift-check vs.
  build-time copy) is chosen in Phase 2 when `portal/static/` needs the files.

## 11. Testing & Verification

There is no JS unit-test harness in this repo today; the frontends are verified manually and by
the eval frame-sync regression. Phase 1 verification:

- **Manual smoke (local console):** open `http://<host>:<port>/?token=<token>` and confirm each
  parity feature: theme toggle persists across reload; tool ticker animates during a tool-using
  turn and finalizes to a count; attach via button + drag + paste, image preview renders, server
  stages the file; skills panel opens, searches, and `/skill` launches; interrupt stops a turn;
  history reloads on refresh; read-panel tabs still work and restyle with the theme.
- **Regression guard:** the existing read panels must be byte-for-byte behaviorally unchanged
  (only the chat block and theme tokens move).
- **Portal untouched:** confirm no files under `portal/` are modified in Phase 1.

## 12. Risks

| Risk | Mitigation |
|------|------------|
| Temporary duplication (portal inline + module) | Accepted, time-boxed to Phase 2; module boundary correct from day one so Phase 2 is deletion. |
| Lifting portal code subtly changes behavior | Port function-by-function with portal as reference; manual smoke against each feature. |
| ES-module `import` fails under the token'd static mount | `local_web.py` serves `/static` via `StaticFiles`; relative `./chat.js` imports resolve under the same mount. Verify MIME `text/javascript` is served. |
| Theme-token move breaks read-panel styling | Tokens are moved, not renamed; panels already reference `--bg`/`--fg`/etc. |

## 13. Build Sequence

1. `chat.css` — move theme tokens + add chat-pane styles (lifted).
2. `connection.js` — socket lifecycle node; unit-exercise by pointing local at it with the chat
   still inline (smallest first step).
3. `chat.js` — core chat (history, streaming, composer, send/stop, offline) with no parity
   features yet; reach behavioral parity with *today's* local chat.
4. Add parity features to `chat.js` one at a time: theme → tool ticker → skills → attachments,
   smoke-testing each.
5. Rewrite `index.html` to host chrome + `#chat-root` + module bootstrap; delete the inline chat.
6. Full manual smoke (§11); confirm `portal/` untouched.
```
