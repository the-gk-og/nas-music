// Equalizer model + Web Audio graph (§6). The graph is:
//   source → preamp → [AutoEq headphone filters] → 10 × peaking biquad → bass-boost low-shelf → [pitch worklet] → stereo matrix
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

/** An AutoEq headphone correction (parametric filters), applied before the 10 bands when the EQ is on. */
export interface HeadphoneProfile {
  id: string;
  name: string;
  source: string;
  preampDb: number;
  filters: { type: 'PK' | 'LSC' | 'HSC'; fc: number; gainDb: number; q: number }[];
}

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
  headphone?: HeadphoneProfile | null;
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
    headphone: null,
  };
}

export function presetFor(bands: number[]): string {
  for (const [name, p] of Object.entries(PRESETS)) if (p.every((v, i) => Math.abs(v - bands[i]) < 0.05)) return name;
  return 'Custom';
}

export function preampGainDb(s: EqSettings): number {
  if (!s.enabled) return 0;
  const base = s.preampMode === 'headroom' ? -6 : s.preampMode === 'boost' ? 1 : 0;
  return base + s.preampDb + (s.headphone ? Math.min(0, s.headphone.preampDb) : 0);
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

// ---- parametric filters (AutoEq) ----

export interface BiquadCoefficients {
  /** Feed-forward b0, b1, b2 and feedback 1, a1, a2, normalised by a0. */
  b: [number, number, number];
  a: [number, number, number];
}

/** RBJ Audio-EQ-Cookbook coefficients. Shelves use Q directly (as AutoEq does), unlike Web Audio's
 *  built-in shelf filters, which ignore Q — hence IIRFilterNode. */
export function rbjCoefficients(type: 'PK' | 'LSC' | 'HSC', fs: number, fc: number, gainDb: number, q: number): BiquadCoefficients {
  const A = Math.pow(10, gainDb / 40);
  const w0 = (2 * Math.PI * Math.min(fc, fs * 0.49)) / fs;
  const cw = Math.cos(w0);
  const alpha = Math.sin(w0) / (2 * Math.max(0.05, q));
  let b0: number, b1: number, b2: number, a0: number, a1: number, a2: number;
  if (type === 'PK') {
    b0 = 1 + alpha * A; b1 = -2 * cw; b2 = 1 - alpha * A;
    a0 = 1 + alpha / A; a1 = -2 * cw; a2 = 1 - alpha / A;
  } else {
    const sq = 2 * Math.sqrt(A) * alpha;
    const sign = type === 'LSC' ? 1 : -1;
    b0 = A * (A + 1 - sign * (A - 1) * cw + sq);
    b1 = sign * 2 * A * (A - 1 - sign * (A + 1) * cw);
    b2 = A * (A + 1 - sign * (A - 1) * cw - sq);
    a0 = A + 1 + sign * (A - 1) * cw + sq;
    a1 = -sign * 2 * (A - 1 + sign * (A + 1) * cw);
    a2 = A + 1 + sign * (A - 1) * cw - sq;
  }
  return { b: [b0 / a0, b1 / a0, b2 / a0], a: [1, a1 / a0, a2 / a0] };
}

/** Magnitude response in dB of a biquad at frequency f (for tests and the EQ curve). */
export function biquadResponseDb(c: BiquadCoefficients, fs: number, f: number): number {
  const w = (2 * Math.PI * f) / fs;
  const re = (k: number[]) => k[0] + k[1] * Math.cos(-w) + k[2] * Math.cos(-2 * w);
  const im = (k: number[]) => k[1] * Math.sin(-w) + k[2] * Math.sin(-2 * w);
  const num = Math.hypot(re(c.b), im(c.b));
  const den = Math.hypot(re(c.a), im(c.a));
  return 20 * Math.log10(num / den);
}

// ---- graph ----

/** The subset of BaseAudioContext the graph needs (lets tests pass a fake). */
export interface AudioCtxLike {
  createGain(): GainNode;
  createBiquadFilter(): BiquadFilterNode;
  createStereoPanner(): StereoPannerNode;
  createChannelSplitter(n?: number): ChannelSplitterNode;
  createChannelMerger(n?: number): ChannelMergerNode;
  createIIRFilter?(feedforward: number[], feedback: number[]): IIRFilterNode;
  readonly sampleRate?: number;
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
  /** AutoEq filters currently in the chain (rebuilt when the profile changes). */
  headphone: AudioNode[];
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

  let headphoneKey = '';
  const setHeadphone = (s: EqSettings) => {
    const prof = s.enabled ? s.headphone : null;
    const key = prof ? JSON.stringify(prof.filters) : '';
    if (key === headphoneKey || !ctx.createIIRFilter) return;
    headphoneKey = key;
    preamp.disconnect();
    for (const n of graph.headphone) n.disconnect();
    const fs = ctx.sampleRate ?? 48000;
    graph.headphone = (prof?.filters ?? []).map((f) => {
      const c = rbjCoefficients(f.type, fs, f.fc, f.gainDb, f.q);
      return ctx.createIIRFilter!(c.b, c.a);
    });
    let prev: AudioNode = preamp;
    for (const n of graph.headphone) {
      prev.connect(n);
      prev = n;
    }
    prev.connect(bands[0]);
  };

  const graph: EqGraph = {
    input: preamp,
    headphone: [],
    output: volume,
    preamp,
    bands,
    bass,
    matrix: { ll, lr, rl, rr },
    panner,
    volume,
    apply(s: EqSettings) {
      const t = ctx.currentTime;
      setHeadphone(s);
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

// ---- pitch shifter (AudioWorklet in public/pitch-worklet.js) ----

/** Served from web/public so it satisfies `script-src 'self'`. */
export const PITCH_WORKLET_URL = 'pitch-worklet.js';

export const semitonesToRatio = (st: number) => Math.pow(2, st / 12);
