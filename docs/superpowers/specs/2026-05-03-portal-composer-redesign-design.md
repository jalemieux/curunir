# Portal Composer Redesign

**Date:** 2026-05-03
**Scope:** `portal/static/index.html` (CSS + small DOM/JS adjustments)

## Problem

The current chat composer in the portal has two visible UX problems:

1. **Tiny typing box.** The textarea defaults to a single 44px row, so the user types into a one-line slit. As soon as the message is more than a sentence, content scrolls horizontally inside a too-small box.
2. **Send button can clip out of view.** With the current `footer { display: flex; max-width: 720px }` layout, the labeled "Send" button sits at the right edge of the centered footer. At certain widths/zooms it visually crowds — or appears to clip — the right edge.

The composer also looks like a generic webform rather than a chat composer, which is at odds with the rest of the dark, polished UI.

## Goals

- Generous default typing area (multiple rows) without making the chat history feel pushed off-screen.
- Send affordance that physically cannot be clipped or pushed off-screen.
- Familiar pattern (matches Claude.ai / ChatGPT) so users don't have to relearn anything.
- Preserve all existing behavior: attach button, staged-attachment chips, auto-grow, Enter-to-send / Shift+Enter for newline, disabled-when-offline state, keyboard focus.

## Non-Goals

- No changes to the message list, header, status indicator, "New" button, or WebSocket/staging logic.
- No new features (voice input, slash-command menu, etc.).
- No mobile-specific redesign beyond what naturally falls out of the new layout.

## Design — Option A: Inline Composer

The composer becomes a single rounded "field" containing:
- The textarea on top (stretches to full width of the field).
- A bottom row with the **attach icon** on the left and the **send icon** on the right.
- A small hint between them: `Enter to send · Shift+Enter for newline`.

Visually:

```
┌──────────────────────────────────────────────────────────┐
│  Type a message…                                         │
│                                                          │
│                                                          │
│  📎              Enter to send · Shift+Enter for newline  ↑  │
└──────────────────────────────────────────────────────────┘
```

The whole field gets a `:focus-within` border highlight (accent color) so users see when they're focused.

### DOM structure

Replace the current `<footer>` children with a single field wrapper:

```html
<footer>
  <input type="file" id="file-input" multiple style="display:none">
  <div id="staged" class="staged-list"></div>
  <div id="composer">
    <textarea id="input" rows="1" placeholder="Message Curunir…"></textarea>
    <div class="composer-row">
      <button id="attach-btn" title="Attach files" aria-label="Attach files">📎</button>
      <span class="composer-hint">Enter to send · Shift+Enter for newline</span>
      <button id="send-btn" title="Send" aria-label="Send">↑</button>
    </div>
  </div>
</footer>
```

Notes:
- `#staged` moves out of its current sibling-of-textarea wrapper and sits **above** the composer (still inside the footer). Staged chips are rare and look better as a row above the composer than crammed inside it.
- `#composer` is the rounded "field" container that holds the textarea + button row.
- IDs `#input`, `#attach-btn`, `#send-btn`, `#file-input`, `#staged` are preserved — JS wiring keeps working unchanged.

### CSS

```css
footer {
  padding: 10px 14px 14px;
  border-top: 1px solid #1a1a22;
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  flex-shrink: 0;
}

#staged.staged-list:empty { display: none; }
#staged.staged-list { margin-bottom: 8px; }

#composer {
  background: #12121c;
  border: 1px solid #2a2a3e;
  border-radius: 14px;
  padding: 10px 10px 8px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
#composer:focus-within { border-color: var(--accent); }

#input {
  background: transparent;
  color: var(--fg);
  border: 0;
  outline: 0;
  resize: none;
  width: 100%;
  font: inherit;
  font-size: 14px;
  line-height: 1.5;
  min-height: 60px;        /* ~3 rows visible by default */
  max-height: 30dvh;       /* unchanged ceiling */
}

.composer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

#attach-btn {
  background: transparent;
  border: 0;
  color: #888;
  width: 32px; height: 32px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
#attach-btn:hover { color: var(--accent); background: #1a1a2e; }

.composer-hint {
  color: #555;
  font-size: 11px;
  flex: 1;
  text-align: right;
  margin-right: 4px;
}

#send-btn {
  background: var(--accent);
  color: white;
  border: 0;
  width: 32px; height: 32px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
#send-btn:disabled { background: #2a2a3e; color: #666; cursor: not-allowed; }
```

### JS changes

The auto-grow handler needs to keep the new minimum visible:

```js
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(
    Math.max(inputEl.scrollHeight, 60),
    window.innerHeight * 0.3
  ) + "px";
});
```

`startNew()` resets the height — change `inputEl.style.height = "auto"` to `inputEl.style.height = ""` so the CSS `min-height: 60px` applies again.

No other JS changes. `send()`, attach wiring, `keydown` handling, `staged` rendering all work as-is because the IDs are preserved.

### Mobile

- The 32×32 icon buttons are below the 44px tap-target guideline. Expand the click area via padding while keeping the visible chip 32×32: `padding: 6px; box-sizing: content-box;` on `#attach-btn` and `#send-btn`. Net click area becomes 44×44, visible chip stays 32×32.
- Remove the hint text on narrow screens (`@media (max-width: 480px) { .composer-hint { display: none; } }`) — keyboard shortcuts don't apply to mobile keyboards anyway.

## Testing

This is a CSS/structure-only change inside one HTML file. Manual verification only:

1. Open the portal in the browser and confirm:
   - Textarea shows ~3 visible rows when empty.
   - Typing past 3 rows causes the textarea to grow up to ~30% viewport height, then scrolls internally.
   - Send icon (↑) is always inside the rounded box — never clipped.
   - `:focus-within` accent border appears when the textarea is focused.
   - Staged attachment chips appear *above* the composer when files are attached.
   - Enter sends; Shift+Enter inserts a newline.
   - Clicking 📎 opens the file picker.
   - When agent is offline, send icon shows the disabled style and clicking it does nothing.
   - "New" in the header clears the conversation and resets the textarea height to ~3 rows.
2. Resize the window narrow (≤480px) — hint disappears, composer still usable, icons remain tappable.
3. Run existing `pytest tests/` — should be unaffected (no Python changes).

No unit tests added; the portal HTML has no test harness today and adding one is out of scope for a CSS pass.

## Risks / Open Questions

- **Send-as-icon discoverability.** First-time users might miss that ↑ means send. The hint text mitigates this on desktop, and Enter-to-send is the primary path. If user testing shows confusion, we can swap the icon for a labeled pill button (`Send →`) without restructuring.
- **Staged chips placement.** Moving `#staged` above the composer changes its current position (currently it's inside the same flex column as the textarea). The visual change is small and the new placement reads more clearly, but worth eyeballing once with an actual attached file.
