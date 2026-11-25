// Now Playing visualiser maths (the canvas drawing is components/Visualiser.svelte).

/** Log-spaced band edges (FFT bin indices) from `fMin` to `fMax` Hz, so bass isn't crammed into one bar. */
export function bandEdges(bars: number, binCount: number, sampleRate: number, fMin = 40, fMax = 16000): number[] {
  const nyquist = sampleRate / 2;
  const hi = Math.min(fMax, nyquist);
  const edges: number[] = [];
  let prev = -1;
  for (let i = 0; i <= bars; i++) {
    const f = fMin * Math.pow(hi / fMin, i / bars);
    let b = Math.round((f / nyquist) * binCount);
    if (b <= prev) b = prev + 1; // every band gets at least one bin
    b = Math.min(b, binCount);
    edges.push(b);
    prev = b;
  }
  return edges;
}

/**
 * Bar heights 0..1 from analyser byte data (getByteFrequencyData). Each bar is the peak of its band;
 * `prev` (same length) gives a gentle fall-off so bars drop smoothly instead of flickering.
 */
export function barLevels(freq: ArrayLike<number>, edges: number[], prev?: number[], fall = 0.08): number[] {
  const out: number[] = [];
  for (let i = 0; i + 1 < edges.length; i++) {
    let peak = 0;
    for (let b = edges[i]; b < Math.max(edges[i + 1], edges[i] + 1) && b < freq.length; b++) peak = Math.max(peak, freq[b]);
    let v = peak / 255;
    if (prev && prev[i] !== undefined) v = Math.max(v, prev[i] - fall);
    out.push(Math.max(0, Math.min(1, v)));
  }
  return out;
}
