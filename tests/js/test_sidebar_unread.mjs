// Zero-dependency node test for sidebar.js: a reply landing in a conversation
// that isn't open (an incoming handoff) must refresh the sidebar and flag the
// row unread until it is opened. Same style as test_connection_outbox.mjs:
//
//   node tests/js/test_sidebar_unread.mjs

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(
  path.resolve(here, "../../src/local_ui/static/sidebar.js")
);
const { isBackgroundFinal, routeUnhandledFrame, markRead, rowClass, agentsWithUnread } = mod;

// Wire the hook the way index.html does, recording what it asked for.
function harness(activeSessionId) {
  const h = { unread: new Map(), requests: 0, snapshots: [], activeSessionId };
  h.route = (msg) => routeUnhandledFrame(msg, {
    unread: h.unread,
    activeSessionId: h.activeSessionId,
    onSnapshot: (list) => h.snapshots.push(list),
    onBackgroundFinal: () => { h.requests += 1; },
  });
  return h;
}

// A final frame for a non-open session triggers a conversations request and
// flags that session unread.
{
  const h = harness("open-session");
  const consumed = h.route({ session_id: "handoff:e1292eda", content: "Noted.", final: true });
  assert.equal(consumed, true);
  assert.equal(h.requests, 1, "must re-request the conversation list");
  assert.equal(h.unread.has("handoff:e1292eda"), true);
}

// Streaming deltas and tool-call frames of that turn do not: the row can't
// exist before the turn's transcript is saved.
{
  const h = harness("open-session");
  assert.equal(h.route({ session_id: "handoff:x", content: "No", delta: true, final: false }), false);
  assert.equal(h.route({ session_id: "handoff:x", tool_calls: [{ name: "read" }], final: false }), false);
  assert.equal(h.requests, 0);
  assert.equal(h.unread.size, 0);
}

// Typed frames for another session (a stale history snapshot, say) are not
// replies; the conversation list snapshot is still delivered.
{
  const h = harness("open-session");
  assert.equal(h.route({ type: "history_snapshot", session_id: "other", messages: [], final: true }), false);
  assert.equal(h.unread.size, 0);
  const list = [{ session_id: "handoff:x", channel: "handoff" }];
  assert.equal(h.route({ type: "conversations_snapshot", session_id: "other", conversations: list }), true);
  assert.deepEqual(h.snapshots, [list]);
  assert.equal(h.route({ type: "conversations_snapshot" }), true);
  assert.deepEqual(h.snapshots[1], []);
  assert.equal(h.requests, 0);
}

// The open conversation's own final frame is never "background".
{
  assert.equal(isBackgroundFinal({ session_id: "s", final: true }, "s"), false);
  assert.equal(isBackgroundFinal({ final: true }, "s"), false, "no session id → not routable");
  assert.equal(isBackgroundFinal(null, "s"), false);
}

// The row is flagged unread, carries the handoff cue, and clears when opened.
{
  const h = harness("open-session");
  h.route({ session_id: "handoff:x", content: "Noted.", final: true });
  const row = { session_id: "handoff:x", channel: "handoff" };
  assert.equal(rowClass(row, "open-session", h.unread), "conv-row unread handoff");
  assert.equal(rowClass({ session_id: "plain", channel: "local_web" }, "open-session", h.unread), "conv-row");

  assert.equal(markRead(h.unread, "handoff:x"), true);
  assert.equal(rowClass(row, "handoff:x", h.unread), "conv-row active handoff");
  assert.equal(rowClass(row, "open-session", h.unread), "conv-row handoff", "stays read after leaving");
  assert.equal(markRead(h.unread, "handoff:x"), false, "idempotent");
}

// A row that is open is shown active, never unread, even if still flagged.
{
  const unread = new Map([["s", ""]]);
  assert.equal(rowClass({ session_id: "s" }, "s", unread), "conv-row active");
}

// Multi-agent: a reply for an agent that isn't selected is remembered under
// that agent so the picker can point at it.
{
  const h = harness("open-session");
  h.route({ session_id: "handoff:a", agent: "keeper", final: true });
  h.route({ session_id: "handoff:b", agent: "coach", final: true });
  h.route({ session_id: "handoff:c", final: true });
  assert.deepEqual([...agentsWithUnread(h.unread, "keeper")], ["coach"]);
  assert.deepEqual([...agentsWithUnread(h.unread, "everyday")].sort(), ["coach", "keeper"]);
  markRead(h.unread, "handoff:b");
  assert.deepEqual([...agentsWithUnread(h.unread, "keeper")], []);
}

console.log("ok - sidebar unread");
