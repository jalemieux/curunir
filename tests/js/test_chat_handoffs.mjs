// Zero-dependency node test for chat.js handoffTargets: which entries of a
// final frame's `handoffs` field get a "Continue with <agent>" button.
//
//   node tests/js/test_chat_handoffs.mjs

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const { handoffTargets } = await import(
  path.resolve(here, "../../src/local_ui/static/chat.js")
);

// Absent / non-array → nothing to offer (every frame before this field existed).
assert.deepEqual(handoffTargets(undefined), []);
assert.deepEqual(handoffTargets(null), []);
assert.deepEqual(handoffTargets("coach"), []);

// Well-formed entries pass through in order.
{
  const list = [
    { agent: "coach", session_id: "handoff:a" },
    { agent: "finance", session_id: "handoff:b" },
  ];
  assert.deepEqual(handoffTargets(list), list);
}

// Malformed entries are dropped, not rendered as a dead button.
assert.deepEqual(
  handoffTargets([
    null,
    { agent: "coach" },
    { session_id: "handoff:a" },
    { agent: "", session_id: "handoff:a" },
    { agent: 7, session_id: "handoff:a" },
    { agent: "coach", session_id: "handoff:ok" },
  ]),
  [{ agent: "coach", session_id: "handoff:ok" }],
);

console.log("ok - chat handoffTargets");
