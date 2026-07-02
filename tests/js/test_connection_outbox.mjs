// Zero-dependency node test for the connection.js outbound path.
//
// There is no JS test runner wired into the repo, so this is a standalone
// assertion script over the *pure* helpers extracted from connection.js
// (routeOutbound / drainOutbox). Run it directly:
//
//   node tests/js/test_connection_outbox.mjs
//
// It exits non-zero on the first failed assertion. The browser loads
// connection.js as an ES module; modern node auto-detects the module syntax,
// so the same file imports here without a build step.

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(
  path.resolve(here, "../../src/local_ui/static/connection.js")
);
const { routeOutbound, drainOutbox } = mod;

// --- routeOutbound ---------------------------------------------------------

// Open socket → send directly, never buffered.
{
  const outbox = [];
  assert.equal(routeOutbound({ durable: true }, true, outbox).action, "send");
  assert.equal(outbox.length, 0, "open socket must not buffer");
}

// Closed socket + durable frame → buffered (so it replays on reconnect).
{
  const outbox = [];
  const frame = { content: "hi", durable: true, client_msg_id: "a" };
  assert.equal(routeOutbound(frame, false, outbox).action, "buffer");
  assert.deepEqual(outbox, [frame], "durable frame must be buffered when closed");
}

// Closed socket + non-durable frame (control/read request) → dropped.
{
  const outbox = [];
  const frame = { command: "history_request" };
  assert.equal(routeOutbound(frame, false, outbox).action, "drop");
  assert.equal(outbox.length, 0, "non-durable frames are not buffered");
}

// --- drainOutbox -----------------------------------------------------------

// Drains in FIFO order while the socket stays open, then empties.
{
  const outbox = [{ n: 1 }, { n: 2 }, { n: 3 }];
  const sent = [];
  drainOutbox(outbox, () => true, (f) => sent.push(f));
  assert.deepEqual(sent, [{ n: 1 }, { n: 2 }, { n: 3 }], "must flush in order");
  assert.equal(outbox.length, 0, "outbox must be empty after a full drain");
}

// Stops as soon as the socket reports closed, leaving the remainder buffered.
{
  const outbox = [{ n: 1 }, { n: 2 }, { n: 3 }];
  const sent = [];
  let open = true;
  drainOutbox(outbox, () => open, (f) => {
    sent.push(f);
    open = false; // socket drops after the first frame
  });
  assert.deepEqual(sent, [{ n: 1 }], "must stop draining once socket closes");
  assert.deepEqual(outbox, [{ n: 2 }, { n: 3 }], "remainder stays buffered");
}

// Round-trip: buffer while closed, then flush on the next open.
{
  const outbox = [];
  routeOutbound({ content: "a", durable: true }, false, outbox);
  routeOutbound({ command: "skills_request" }, false, outbox); // dropped
  routeOutbound({ content: "b", durable: true }, false, outbox);
  const sent = [];
  drainOutbox(outbox, () => true, (f) => sent.push(f));
  assert.deepEqual(
    sent.map((f) => f.content),
    ["a", "b"],
    "only durable frames replay, in order",
  );
}

console.log("connection.js outbox: all assertions passed");
