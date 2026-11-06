// Captures mono float audio, posts 40 ms Int16 PCM chunks, and runs a simple energy VAD
// whose speech_end message is the fallback "user stopped talking" timestamp.
const CHUNK = 640;            // samples at 16 kHz = 40 ms
const THRESHOLD = 0.015;      // RMS above this counts as voice
const SILENCE_S = 0.6;        // this much silence after voice ends the utterance

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(CHUNK);
    this.n = 0;
    this.speaking = false;
    this.lastVoice = 0;
    this.lastLevel = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    let energy = 0;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      energy += s * s;
      this.buf[this.n++] = s < 0 ? s * 32768 : s * 32767;
      if (this.n === CHUNK) {
        this.port.postMessage({ type: "pcm", buf: this.buf.buffer }, [this.buf.buffer]);
        this.buf = new Int16Array(CHUNK);
        this.n = 0;
      }
    }
    const rms = Math.sqrt(energy / ch.length);
    if (currentTime - this.lastLevel > 0.1) {
      this.lastLevel = currentTime;
      this.port.postMessage({ type: "level", rms, speaking: this.speaking });
    }
    if (rms > THRESHOLD) {
      if (!this.speaking) { this.speaking = true; this.port.postMessage({ type: "speech_start" }); }
      this.lastVoice = currentTime;
    } else if (this.speaking && currentTime - this.lastVoice > SILENCE_S) {
      this.speaking = false;
      this.port.postMessage({ type: "speech_end", silence_ms: SILENCE_S * 1000 });
    }
    return true;
  }
}
registerProcessor("mic", MicProcessor);
