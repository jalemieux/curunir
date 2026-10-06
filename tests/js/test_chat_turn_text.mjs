// Zero-dependency node test for the chat pane's turn-text helpers.
//
// Text the agent streamed before a tool call used to be overwritten when the
// final reply arrived. The pure helpers exported from chat.js keep it as
// "interim" text instead, split at the `segment` the worker stamps on text
// deltas and on the final reply.
//
//   node tests/js/test_chat_turn_text.mjs

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(path.resolve(here, "../../src/local_ui/static/chat.js"));
const { newTurnText, applyTextFrame, applyHistoryText } = mod;

const run = (frames) => frames.reduce(applyTextFrame, newTurnText());
const delta = (content, segment) => ({ content, delta: true, final: false, segment });
const tool = (name) => ({ content: "", tool_calls: [name], final: false });

// The reported case: text, a tool round, then the reply. The interim sentence
// survives the final frame and the body is the reply alone.
{
  const t = run([
    delta("I'll check your ", 0), delta("schedule first.", 0),
    tool("read schedule.md"),
    delta("You are free ", 1), delta("at 3pm.", 1),
    { content: "You are free at 3pm.", final: true, segment: 1 },
  ]);
  assert.deepEqual(t.interim, ["I'll check your schedule first."]);
  assert.equal(t.raw, "You are free at 3pm.");
}

// Several tool rounds: each stretch is its own interim entry, in order.
{
  const t = run([
    delta("First.", 0), tool("a"), delta("Second.", 1), tool("b"), tool("c"),
    delta("Done.", 2), { content: "Done.", final: true, segment: 2 },
  ]);
  assert.deepEqual(t.interim, ["First.", "Second."]);
  assert.equal(t.raw, "Done.");
}

// While the turn is live the body shows the stretch being streamed.
{
  const t = run([delta("First.", 0), tool("a"), delta("Sec", 1)]);
  assert.deepEqual(t.interim, ["First."]);
  assert.equal(t.raw, "Sec");
}

// The turn ended on a tool round (interrupt, error): the final reply is a new
// segment, so the last streamed stretch is kept rather than replaced.
{
  const t = run([
    delta("Let me look.", 0), tool("bash"),
    { content: "(interrupted)", final: true, segment: 1 },
  ]);
  assert.deepEqual(t.interim, ["Let me look."]);
  assert.equal(t.raw, "(interrupted)");
}

// No tool round: one segment, and the final frame replaces the streamed copy
// of the same text instead of duplicating it.
{
  const t = run([delta("Hel", 0), delta("lo", 0), { content: "Hello", final: true, segment: 0 }]);
  assert.deepEqual(t.interim, []);
  assert.equal(t.raw, "Hello");
}

// Non-streaming turn: only the final frame carries text.
{
  const t = run([tool("read"), { content: "Answer.", final: true, segment: 0 }]);
  assert.deepEqual(t.interim, []);
  assert.equal(t.raw, "Answer.");
}

// A server that sends no `segment` keeps the old behaviour: final overwrites.
{
  const t = run([
    { content: "I'll check.", delta: true, final: false }, tool("read"),
    { content: "Free at 3pm.", delta: true, final: false },
    { content: "Free at 3pm.", final: true },
  ]);
  assert.deepEqual(t.interim, []);
  assert.equal(t.raw, "Free at 3pm.");
}

// Frames without text leave the state alone: tool frames, and a final frame
// with empty content (the body keeps what was streamed).
{
  const t = run([delta("Kept.", 0), tool("read"), { content: "", final: true, segment: 1 }]);
  assert.deepEqual(t.interim, []);
  assert.equal(t.raw, "Kept.");
}

// Whitespace-only stretches are not worth an interim entry.
{
  const t = run([delta("\n\n", 0), tool("read"), delta("Answer.", 1)]);
  assert.deepEqual(t.interim, []);
  assert.equal(t.raw, "Answer.");
}

// Reload agrees with live: the transcript's assistant messages of one turn
// fold to the same state the frames produced.
{
  const live = run([
    delta("First.", 0), tool("a"), delta("Second.", 1), tool("b"),
    delta("Done.", 2), { content: "Done.", final: true, segment: 2 },
  ]);
  const history = [
    { role: "assistant", content: "First.", tool_calls: ["a"] },
    { role: "assistant", content: "", tool_calls: ["x"] },
    { role: "assistant", content: "Second.", tool_calls: ["b"] },
    { role: "assistant", content: "Done." },
  ];
  const reloaded = history.reduce((t, m) => applyHistoryText(t, m.content), newTurnText());
  assert.deepEqual(reloaded.interim, live.interim);
  assert.equal(reloaded.raw, live.raw);
}

console.log("test_chat_turn_text: all assertions passed");
