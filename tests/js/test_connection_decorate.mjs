// Zero-dependency node test for connection.js decorateFrame (multi-agent
// `agent` address merged into every outbound frame). Same style as
// test_connection_outbox.mjs:
//
//   node tests/js/test_connection_decorate.mjs

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(
  path.resolve(here, "../../src/local_ui/static/connection.js")
);
const { decorateFrame } = mod;

// No decorate → the very same object comes back (single-agent path allocates nothing).
{
  const frame = { content: "hi", session_id: "s" };
  assert.equal(decorateFrame(frame, undefined), frame);
  assert.equal(decorateFrame(frame, null), frame);
}

// decorate returning {} (default agent) → same object, no field added.
{
  const frame = { content: "hi" };
  const out = decorateFrame(frame, () => ({}));
  assert.equal(out, frame);
  assert.equal("agent" in out, false);
}

// decorate returning {agent} → merged copy; the original is untouched.
{
  const frame = { content: "hi", session_id: "s" };
  const out = decorateFrame(frame, () => ({ agent: "finance" }));
  assert.notEqual(out, frame, "must not mutate the caller's frame");
  assert.deepEqual(out, { content: "hi", session_id: "s", agent: "finance" });
  assert.equal("agent" in frame, false);
}

// The frame's own explicit field wins over the decoration.
{
  const out = decorateFrame({ command: "clear", agent: "everyday" }, () => ({ agent: "finance" }));
  assert.equal(out.agent, "everyday");
}

// A falsy frame passes through.
{
  assert.equal(decorateFrame(null, () => ({ agent: "x" })), null);
}

console.log("connection.js decorate: all assertions passed");
