// Plays queued 24 kHz Int16 PCM chunks. "mark" tags following chunks with a turn id so the
// first sample of each turn can be reported; "clear" drops everything queued (interruption).
class Player extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.cur = null;
    this.pos = 0;
    this.turn = null;
    this.reported = null;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === "audio") {
        const i16 = new Int16Array(m.pcm);
        const f = new Float32Array(i16.length);
        for (let i = 0; i < i16.length; i++) f[i] = i16[i] / 32768;
        this.queue.push({ f, turn: this.turn });
      } else if (m.type === "mark") {
        this.turn = m.turn;
      } else if (m.type === "clear") {
        this.queue = [];
        this.cur = null;
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
        this.cur = item.f;
        this.pos = 0;
        if (item.turn !== null && item.turn !== this.reported) {
          this.reported = item.turn;
          this.port.postMessage({ type: "playback_start", turn: item.turn });
        }
      }
      const n = Math.min(out.length - i, this.cur.length - this.pos);
      out.set(this.cur.subarray(this.pos, this.pos + n), i);
      i += n;
      this.pos += n;
      if (this.pos >= this.cur.length) this.cur = null;
    }
    for (; i < out.length; i++) out[i] = 0;
    return true;
  }
}
registerProcessor("player", Player);
