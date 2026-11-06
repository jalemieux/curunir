// Voice mode worklets (#614, portal/static/voice/): run both processors in node at a
// 48 kHz device rate (what iPhones use). The mic path must yield 16 kHz PCM, the
// player path must play 24 kHz PCM at the device rate, report the first sample of a
// turn once, report each slice as played, and drop queued audio on clear.
// node tests/js/test_voice_worklets.mjs
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

function load(file, rate) {
  const registered = {};
  const src = readFileSync(new URL(`../../portal/static/voice/${file}`, import.meta.url), "utf8");
  const fn = new Function("AudioWorkletProcessor", "registerProcessor", "sampleRate", "currentTime", src);
  fn(class { constructor() { this.port = { postMessage: () => {}, onmessage: null }; } },
     (name, cls) => (registered[name] = cls), rate, 0);
  return registered;
}

function sine(hz, rate, n) { return Float32Array.from({ length: n }, (_, i) => 0.5 * Math.sin((2 * Math.PI * hz * i) / rate)); }

{
  const { mic: Mic } = load("mic-worklet.js", 48000);
  const m = new Mic();
  const chunks = [];
  m.port = { postMessage: (msg) => { if (msg.type === "pcm") chunks.push(new Int16Array(msg.buf)); } };
  const input = sine(440, 48000, 48000);
  for (let i = 0; i < input.length; i += 128) m.process([[input.subarray(i, i + 128)]]);
  const out = Int16Array.from(chunks.flatMap((c) => Array.from(c)));
  assert.ok(Math.abs(out.length - 16000) <= 640, `expected ~16000 samples, got ${out.length}`);
  assert.ok(chunks.every((c) => c.length === 640), "40 ms chunks");
  const expected = sine(440, 16000, out.length);
  let err = 0;
  for (let i = 0; i < out.length; i++) err += Math.abs(out[i] / 32768 - expected[i]);
  assert.ok(err / out.length < 0.01, `mean resampling error ${err / out.length}`);
}

{
  const { player: Player } = load("player-worklet.js", 48000);
  const p = new Player();
  const reports = [];
  const port = p.port;
  p.port = { postMessage: (m) => reports.push(m), onmessage: port.onmessage };
  const pcm = Int16Array.from(sine(440, 24000, 2400), (v) => v * 32767); // 100 ms
  p.port.onmessage({ data: { type: "mark", turn: "t7" } });
  p.port.onmessage({ data: { type: "audio", pcm: pcm.buffer.slice(0), seq: 1 } });
  p.port.onmessage({ data: { type: "audio", pcm: pcm.buffer.slice(0), seq: 2 } });
  const out = [];
  for (let i = 0; i < 100; i++) { const buf = new Float32Array(128); p.process([], [[buf]]); out.push(...buf); }
  const expected = sine(440, 48000, 4800);
  let err = 0;
  for (let i = 0; i < 4800; i++) err += Math.abs(out[i] - expected[i]);
  assert.ok(err / 4800 < 0.01, `mean playback error ${err / 4800}`);
  assert.ok(out.slice(9600).every((v) => v === 0), "silence after both slices");
  assert.deepEqual(reports, [{ type: "playback_start", turn: "t7" }, { type: "played", seq: 1 }, { type: "played", seq: 2 }]);
  p.port.onmessage({ data: { type: "audio", pcm: pcm.buffer.slice(0), seq: 3 } });
  p.port.onmessage({ data: { type: "clear" } });
  const buf = new Float32Array(128); p.process([], [[buf]]);
  assert.ok(buf.every((v) => v === 0), "clear drops queued audio");
  assert.equal(reports.length, 3, "a cleared slice is never reported played");
}
console.log("voice worklet tests ok");
