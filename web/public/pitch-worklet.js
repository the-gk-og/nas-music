// Pitch shifter AudioWorklet (two crossfaded delay taps). Loaded from 'self' so it passes the player CSP.
/* global AudioWorkletProcessor, registerProcessor */
class PitchShifter extends AudioWorkletProcessor {
  static get parameterDescriptors() { return [{ name: 'ratio', defaultValue: 1, minValue: 0.5, maxValue: 2 }]; }
  constructor() {
    super();
    this.size = 2048; this.buf = [new Float32Array(8192), new Float32Array(8192)]; this.w = 0; this.phase = 0;
  }
  process(inputs, outputs, params) {
    const input = inputs[0], output = outputs[0];
    if (!input || !input.length) return true;
    const ratio = params.ratio[0];
    const n = output[0].length, size = this.size, mask = 8191;
    for (let ch = 0; ch < output.length; ch++) {
      const inp = input[Math.min(ch, input.length - 1)], out = output[ch], buf = this.buf[ch & 1];
      let w = this.w, phase = this.phase;
      for (let i = 0; i < n; i++) {
        buf[w & mask] = inp[i];
        if (Math.abs(ratio - 1) < 1e-3) { out[i] = inp[i]; w++; continue; }
        phase = (phase + (1 - ratio) + size) % size;
        const p2 = (phase + size / 2) % size;
        const g1 = Math.sin(Math.PI * phase / size), g2 = Math.sin(Math.PI * p2 / size);
        const r1 = (w - phase + 8192) & mask, r2 = (w - p2 + 8192) & mask;
        out[i] = buf[r1] * g1 * g1 + buf[r2] * g2 * g2;
        w++;
      }
      if (ch === output.length - 1) { this.w = w; this.phase = phase; }
    }
    return true;
  }
}
registerProcessor('nm-pitch', PitchShifter);
