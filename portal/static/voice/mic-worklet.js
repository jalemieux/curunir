// Mic capture for voice mode (#614). Takes mono float audio at the device rate,
// resamples to 16 kHz with linear interpolation (iOS changes its rate when the mic
// opens, so the context rate is never assumed), and posts 40 ms Int16 PCM chunks.
const OUT_RATE = 16000;
const CHUNK = 640; // samples at 16 kHz = 40 ms

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(CHUNK);
    this.n = 0;
    this.step = sampleRate / OUT_RATE; // input samples per output sample
    this.pos = 0;                      // fractional read position into the current block
    this.prev = 0;                     // last sample of the previous block
    this.lastLevel = 0;
  }

  resample(ch) {
    if (this.step === 1) return ch;
    const out = [];
    let pos = this.pos;
    while (pos < ch.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const a = i < 0 ? this.prev : ch[i];
      const b = i + 1 < ch.length ? ch[i + 1] : ch[ch.length - 1];
      out.push(a + (b - a) * frac);
      pos += this.step;
    }
    this.pos = pos - ch.length;
    this.prev = ch[ch.length - 1];
    return out;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    let energy = 0;
    const samples = this.resample(ch);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      energy += s * s;
      this.buf[this.n++] = s < 0 ? s * 32768 : s * 32767;
      if (this.n === CHUNK) {
        this.port.postMessage({ type: "pcm", buf: this.buf.buffer }, [this.buf.buffer]);
        this.buf = new Int16Array(CHUNK);
        this.n = 0;
      }
    }
    if (currentTime - this.lastLevel > 0.1) {
      this.lastLevel = currentTime;
      this.port.postMessage({ type: "level", rms: Math.sqrt(energy / Math.max(1, samples.length)) });
    }
    return true;
  }
}
registerProcessor("mic", MicProcessor);
