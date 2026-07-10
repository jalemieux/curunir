// Zero-dependency node test for the chat pane's stick-to-bottom logic.
//
// Streaming used to force scrollTop = scrollHeight on every delta, yanking
// the pane back down when the user scrolled up mid-stream. The fix keeps a
// "pinned" flag: auto-scroll happens only while the user is at (or near)
// the bottom, or when the caller forces it (own send, history load).
//
//   node tests/js/test_chat_scroll.mjs
//
// Tests the pure helpers exported from chat.js: computeScrollPinned
// (is the viewport near the bottom?) and shouldAutoScroll (does this
// render pass get to move the viewport?).

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(path.resolve(here, "../../src/local_ui/static/chat.js"));
const { computeScrollPinned, shouldAutoScroll } = mod;

// --- computeScrollPinned ----------------------------------------------------

// Exactly at the bottom → pinned.
assert.equal(computeScrollPinned(1000, 1600, 600), true, "at bottom is pinned");

// Within the threshold of the bottom (sub-pixel rounding, tiny drift) → pinned.
assert.equal(computeScrollPinned(960, 1600, 600), true, "within threshold is pinned");

// Scrolled up past the threshold → not pinned.
assert.equal(computeScrollPinned(500, 1600, 600), false, "scrolled up is not pinned");

// Content shorter than the viewport (nothing to scroll) → pinned.
assert.equal(computeScrollPinned(0, 400, 600), true, "unscrollable pane is pinned");

// Custom threshold is honored.
assert.equal(computeScrollPinned(900, 1600, 600, 100), true, "custom threshold pins");
assert.equal(computeScrollPinned(899, 1600, 600, 100), false, "past custom threshold does not");

// --- shouldAutoScroll -------------------------------------------------------

// Pinned reader follows the stream.
assert.equal(shouldAutoScroll(true, false), true, "pinned follows stream");

// Reader who scrolled up is left alone by streaming deltas.
assert.equal(shouldAutoScroll(false, false), false, "scrolled-up reader is not yanked");

// Forced scroll (own send, history load) always wins.
assert.equal(shouldAutoScroll(false, true), true, "force overrides scrolled-up state");
assert.equal(shouldAutoScroll(true, true), true, "force while pinned scrolls");

console.log("test_chat_scroll: all assertions passed");
