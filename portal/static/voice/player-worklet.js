// Playback for voice mode (#614). Queues 24 kHz Int16 PCM slices and plays them at
// the device rate (linear resampling). "mark" tags the slices that follow with a turn
// id and the first sample of each turn is reported; each slice reports "played" with
// its seq when it has fully left the queue (the interrupt frame carries the last one);
// "clear" drops everything queued (barge-in).
const IN_RATE = 24000;

class Player extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.cur = null;
    this.pos = 0;
    this.step = IN_RATE / sampleRate;
    this.turn = null;
    this.reported = null;
    this.last = null;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === "audio") {
        const i16 = new Int16Array(m.pcm);
        const f = new Float32Array(i16.length);
        for (let i = 0; i < i16.length; i++) f[i] = i16[i] / 32768;
        this.queue.push({ f, turn: this.turn, seq: m.seq });
      } else if (m.type === "mark") {
        this.turn = m.turn;
      } else if (m.type === "clear") {
        this.queue = [];
        this.cur = null;
        this.last = null;
        this.pos = 0;
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    let i = 0;
    while (i < out.length) {
      if (!this.cur) {
        const item = this.queue.shift();
        if (!item) break;
        this.cur = item;
        this.pos = Math.max(0, this.pos - (this.last ? this.last.f.length : 0));
        this.last = null;
        if (item.turn !== null && item.turn !== this.reported) {
          this.reported = item.turn;
          this.port.postMessage({ type: "playback_start", turn: item.turn });
        }
      }
      const src = this.cur.f;
      while (i < out.length && this.pos < src.length) {
        const k = Math.floor(this.pos);
        const frac = this.pos - k;
        const a = src[k];
        const b = k + 1 < src.length ? src[k + 1] : a;
        out[i++] = a + (b - a) * frac;
        this.pos += this.step;
      }
      if (this.pos >= src.length) {
        this.port.postMessage({ type: "played", seq: this.cur.seq });
        this.last = this.cur;
        this.cur = null;
      }
    }
    for (; i < out.length; i++) out[i] = 0;
    return true;
  }
}
registerProcessor("player", Player);
