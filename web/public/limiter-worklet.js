// Look-ahead peak limiter AudioWorklet, the same algorithm as Android's LookaheadLimiter. Loaded from 'self' so it
// passes the player CSP. Stereo-linked: audio is delayed 3 ms; the gain any sample in that window needs (sliding
// minimum) drops instantly and recovers over 150 ms, then a 3 ms moving average smooths the drop. Every value in that
// average is ≤ what the delayed sample needs, so peaks are turned down smoothly and never clipped.
/* global AudioWorkletProcessor, registerProcessor, sampleRate */
class LookaheadLimiter extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ceiling = 0.98;
    const L = Math.max(1, Math.round(0.003 * sampleRate));
    this.L = L;
    this.delay = [new Float32Array(L), new Float32Array(L)];
    this.pos = 0;
    this.cap = L + 2;
    this.dqN = new Float64Array(this.cap);
    this.dqG = new Float64Array(this.cap);
    this.head = 0;
    this.size = 0;
    this.n = 0;
    this.held = 1;
    this.avg = new Float64Array(L).fill(1);
    this.sum = L;
    this.release = 1 - Math.exp(-1 / (0.15 * sampleRate));
  }
  process(inputs, outputs) {
    const input = inputs[0], output = outputs[0];
    const out0 = output[0], out1 = output[1] || output[0];
    const frames = out0.length;
    const in0 = input && input.length ? input[0] : null;
    const in1 = input && input.length > 1 ? input[1] : in0;
    const { L, cap, ceiling, dqN, dqG, avg, release } = this;
    const d0 = this.delay[0], d1 = this.delay[1];
    for (let i = 0; i < frames; i++) {
      const a = in0 ? in0[i] : 0, b = in1 ? in1[i] : 0;
      const peak = Math.max(Math.abs(a), Math.abs(b));
      const need = peak > ceiling ? ceiling / peak : 1;
      while (this.size > 0 && dqG[(this.head + this.size - 1) % cap] >= need) this.size--;
      const t = (this.head + this.size) % cap;
      dqN[t] = this.n; dqG[t] = need; this.size++;
      while (dqN[this.head] <= this.n - (L + 1)) { this.head = (this.head + 1) % cap; this.size--; }
      this.held = Math.min(dqG[this.head], this.held + (1 - this.held) * release);
      const p = this.pos;
      this.sum += this.held - avg[p]; avg[p] = this.held;
      const env = Math.min(1, Math.max(0, this.sum / L));
      const o0 = d0[p] * env, o1 = d1[p] * env;
      d0[p] = a; d1[p] = b;
      out0[i] = Math.max(-ceiling, Math.min(ceiling, o0));
      if (out1 !== out0) out1[i] = Math.max(-ceiling, Math.min(ceiling, o1));
      this.pos = (p + 1) % L;
      this.n++;
    }
    if (this.n % 65536 < frames) { let s = 0; for (let i = 0; i < L; i++) s += avg[i]; this.sum = s; }
    return true;
  }
}
registerProcessor('hb-limiter', LookaheadLimiter);
