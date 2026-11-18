// Equalizer model + Web Audio graph (§6). The graph is:
//   source → preamp → 10 × peaking biquad → bass-boost low-shelf → [pitch worklet] → stereo matrix
//   (width / mono) → balance (StereoPanner) → volume → destination
// The stereo matrix does both "surround" widening and mono: L' = a·L + b·R, R' = a·R + b·L with
// a = (1+w)/2, b = (1−w)/2 and w = 0 (mono) … 1 (normal) … 2 (widest).

export const EQ_FREQS = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000] as const;
export const EQ_LABELS = ['31.5', '63', '125', '250', '500', '1 k', '2 k', '4 k', '8 k', '16 k'];
export const EQ_MAX_DB = 12;
export const BAND_Q = 1.41; // ~1 octave

export const PRESETS: Record<string, number[]> = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  Bass: [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
  Vocal: [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1],
  Treble: [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
  Rock: [5, 4, 2, -1, -2, -1, 1, 3, 4, 5],
  Pop: [-1, 1, 3, 4, 3, 0, -1, -1, 1, 2],
  Classical: [4, 3, 2, 1, -1, -1, 0, 2, 3, 4],
};
export const PRESET_NAMES = [...Object.keys(PRESETS), 'Custom'];

export type PreampMode = 'headroom' | 'off' | 'boost';

export interface EqSettings {
  enabled: boolean;
  preset: string;
  bands: number[];
  preampMode: PreampMode;
  /** Extra preamp from the slider, −12…+12 dB, added to the mode's base. */
  preampDb: number;
  bassBoost: number; // 0..100 %
  surround: number; // 0..100 %
  volume: number; // 0..1
  mono: boolean;
  balance: number; // −1 (left) … +1 (right)
}

export function defaultEq(): EqSettings {
  return {
    enabled: false,
    preset: 'Flat',
    bands: [...PRESETS.Flat],
    preampMode: 'off',
    preampDb: 0,
    bassBoost: 0,
    surround: 0,
    volume: 1,
    mono: false,
    balance: 0,
  };
}

export function presetFor(bands: number[]): string {
  for (const [name, p] of Object.entries(PRESETS)) if (p.every((v, i) => Math.abs(v - bands[i]) < 0.05)) return name;
  return 'Custom';
}

export function preampGainDb(s: EqSettings): number {
  if (!s.enabled) return 0;
  const base = s.preampMode === 'headroom' ? -6 : s.preampMode === 'boost' ? 1 : 0;
  return base + s.preampDb;
}

/** Bass boost: low shelf at 100 Hz, 0–100 % → 0–12 dB. */
export function bassBoostDb(s: EqSettings): number {
  return s.enabled ? (Math.max(0, Math.min(100, s.bassBoost)) / 100) * 12 : 0;
}

export function stereoWidth(s: EqSettings): number {
  if (s.mono) return 0;
  return s.enabled ? 1 + Math.max(0, Math.min(100, s.surround)) / 100 : 1;
}

export function matrixCoefficients(width: number): { same: number; cross: number } {
  return { same: (1 + width) / 2, cross: (1 - width) / 2 };
}

export const dbToGain = (db: number) => Math.pow(10, db / 20);

/** "Left:100, Right:100" as in the reference screenshot. */
export function balanceLabel(balance: number): string {
  const l = Math.round(Math.min(1, 1 - balance) * 100);
  const r = Math.round(Math.min(1, 1 + balance) * 100);
  return `Left:${l}, Right:${r}`;
}

// ---- graph ----

/** The subset of BaseAudioContext the graph needs (lets tests pass a fake). */
export interface AudioCtxLike {
  createGain(): GainNode;
  createBiquadFilter(): BiquadFilterNode;
  createStereoPanner(): StereoPannerNode;
  createChannelSplitter(n?: number): ChannelSplitterNode;
  createChannelMerger(n?: number): ChannelMergerNode;
  readonly destination: AudioNode;
  readonly currentTime: number;
}

export interface EqGraph {
  input: AudioNode;
  output: AudioNode;
  preamp: GainNode;
  bands: BiquadFilterNode[];
  bass: BiquadFilterNode;
  matrix: { ll: GainNode; lr: GainNode; rl: GainNode; rr: GainNode };
  panner: StereoPannerNode;
  volume: GainNode;
  apply(s: EqSettings): void;
  /** Insert an extra node (e.g. the pitch shifter) between the bass shelf and the stereo matrix. */
  insert(node: AudioNode): void;
}

function setParam(p: AudioParam, v: number, t: number) {
  // setTargetAtTime avoids zipper noise; fall back to plain assignment for fakes.
  if (typeof p.setTargetAtTime === 'function') p.setTargetAtTime(v, t, 0.015);
  else p.value = v;
}

export function buildEqGraph(ctx: AudioCtxLike): EqGraph {
  const preamp = ctx.createGain();
  const bands = EQ_FREQS.map((f) => {
    const b = ctx.createBiquadFilter();
    b.type = 'peaking';
    b.frequency.value = f;
    b.Q.value = BAND_Q;
    b.gain.value = 0;
    return b;
  });
  const bass = ctx.createBiquadFilter();
  bass.type = 'lowshelf';
  bass.frequency.value = 100;
  bass.gain.value = 0;

  const splitter = ctx.createChannelSplitter(2);
  const merger = ctx.createChannelMerger(2);
  const ll = ctx.createGain();
  const lr = ctx.createGain(); // L → R
  const rl = ctx.createGain(); // R → L
  const rr = ctx.createGain();
  splitter.connect(ll, 0);
  splitter.connect(lr, 0);
  splitter.connect(rl, 1);
  splitter.connect(rr, 1);
  ll.connect(merger, 0, 0);
  rl.connect(merger, 0, 0);
  rr.connect(merger, 0, 1);
  lr.connect(merger, 0, 1);

  const panner = ctx.createStereoPanner();
  const volume = ctx.createGain();

  preamp.connect(bands[0]);
  for (let i = 0; i < bands.length - 1; i++) bands[i].connect(bands[i + 1]);
  bands[bands.length - 1].connect(bass);
  bass.connect(splitter);
  merger.connect(panner);
  panner.connect(volume);
  volume.connect(ctx.destination);

  const graph: EqGraph = {
    input: preamp,
    output: volume,
    preamp,
    bands,
    bass,
    matrix: { ll, lr, rl, rr },
    panner,
    volume,
    apply(s: EqSettings) {
      const t = ctx.currentTime;
      setParam(preamp.gain, dbToGain(preampGainDb(s)), t);
      bands.forEach((b, i) => setParam(b.gain, s.enabled ? Math.max(-EQ_MAX_DB, Math.min(EQ_MAX_DB, s.bands[i] ?? 0)) : 0, t));
      setParam(bass.gain, bassBoostDb(s), t);
      const { same, cross } = matrixCoefficients(stereoWidth(s));
      setParam(ll.gain, same, t);
      setParam(rr.gain, same, t);
      setParam(lr.gain, cross, t);
      setParam(rl.gain, cross, t);
      setParam(panner.pan, Math.max(-1, Math.min(1, s.balance)), t);
      setParam(volume.gain, Math.max(0, Math.min(1, s.volume)), t);
    },
    insert(node: AudioNode) {
      bass.disconnect();
      bass.connect(node);
      node.connect(splitter);
    },
  };
  return graph;
}

// ---- per-output profiles ----

const PROFILES_KEY = 'nm.eq.profiles';

export function loadProfiles(storage: Pick<Storage, 'getItem'> | null): Record<string, EqSettings> {
  try {
    const raw = storage?.getItem(PROFILES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, Partial<EqSettings>>;
    const out: Record<string, EqSettings> = {};
    for (const [k, v] of Object.entries(parsed)) out[k] = { ...defaultEq(), ...v };
    return out;
  } catch {
    return {};
  }
}

export function saveProfiles(storage: Pick<Storage, 'setItem'> | null, profiles: Record<string, EqSettings>) {
  try {
    storage?.setItem(PROFILES_KEY, JSON.stringify(profiles));
  } catch {
    /* storage full / blocked */
  }
}

// ---- pitch shifter (AudioWorklet, two crossfaded delay taps) ----

export const PITCH_WORKLET = `
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
`;

export const semitonesToRatio = (st: number) => Math.pow(2, st / 12);
