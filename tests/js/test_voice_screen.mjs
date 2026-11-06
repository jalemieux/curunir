// Voice screen helpers (#617): node tests/js/test_voice_screen.mjs
import { createRequire } from "node:module";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { orbState, modePill, foldCaption, timingLine, playbackDone } = require("../../portal/static/voice/screen.js");

// orb and label per state and mode
assert.deepEqual(orbState("ready"), { cls: "ready", label: "listening for you" });
assert.deepEqual(orbState("ready", { ptt: true }), { cls: "ready", label: "hold the button and talk" });
assert.deepEqual(orbState("ready", { ptt: true, talking: true }), { cls: "listening", label: "hearing you" });
assert.equal(orbState("listening").cls, "listening");
assert.equal(orbState("speaking").cls, "speaking");
assert.equal(orbState("loading").cls, "loading");
assert.equal(orbState("off").cls, "off");
assert.equal(orbState("error", { detail: "mic denied" }).label, "voice unavailable: mic denied");
assert.equal(orbState("mystery").label, "mystery");

// mode pill
assert.equal(modePill("local", true), "speech on your box · hands-free");
assert.equal(modePill("openai", false), "speech via OpenAI · hold to talk");
assert.equal(modePill(undefined, false), "speech on the agent · hold to talk");

// captions fold like the chat does
let c = foldCaption(null, { delta: true, content: "Two things", segment: 0 });
c = foldCaption(c, { delta: true, content: ". A 9 o'clock", segment: 0 });
assert.equal(c.text, "Two things. A 9 o'clock");
c = foldCaption(c, { delta: true, content: "Done.", segment: 1 });  // after a tool round
assert.equal(c.text, "Two things. A 9 o'clock Done.");
c = foldCaption(c, { final: true, content: "Done, moved it." });
assert.deepEqual(c, { text: "Done, moved it.", segment: null });
assert.equal(foldCaption({ text: "Partial", segment: 0 }, { final: true, content: "(interrupted)" }).text, "Partial");
assert.equal(foldCaption({ text: "x", segment: 0 }, { tool_calls: ["Read a.py"], final: false }).text, "x");

// timings
assert.deepEqual(timingLine(1420, { transcript: 510, first_text_delta: 930, first_audio_chunk_sent: 1210, audio_final: 4000 }),
  ["you stopped → first audio 1.4 s", "transcript 0.5 · first text 0.9 · first audio sent 1.2 s after speech end"]);
assert.deepEqual(timingLine(undefined, {}), []);
assert.deepEqual(timingLine(800, null), ["you stopped → first audio 0.8 s"]);
// speaking lasts until the last slice has played, not until synthesis ended
assert.equal(playbackDone({ closed: false, lastDataSeq: 7, playedSeq: 7 }), false);
assert.equal(playbackDone({ closed: true, lastDataSeq: 7, playedSeq: 2 }), false);
assert.equal(playbackDone({ closed: true, lastDataSeq: 7, playedSeq: 7 }), true);
assert.equal(playbackDone({ closed: true, lastDataSeq: 0, playedSeq: 0 }), true);  // a reply with no audio at all
console.log("voice screen tests ok");
