// Equalizer model + Web Audio graph (§6). The graph is:
//   source → preamp → [AutoEq headphone filters] → 10 × peaking biquad → [parametric bands] → bass-boost low-shelf → [pitch worklet] → stereo matrix
//   (width / mono) → balance (StereoPanner) → limiter → post (RTA tap) → volume → destination
// The preamp includes automatic headroom: the biggest boost of the whole curve is taken off, so boosts never clip
// (same maths as the Android EqEngine). The user's preamp is a trim on top; the limiter catches what's left.
// The stereo matrix does both "surround" widening and mono: L' = a·L + b·R, R' = a·R + b·L with
// a = (1+w)/2, b = (1−w)/2 and w = 0 (mono) … 1 (normal) … 2 (widest).

export const EQ_FREQS = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000] as const;
export const EQ_LABELS = ['31.5', '63', '125', '250', '500', '1 k', '2 k', '4 k', '8 k', '16 k'];
export const EQ_MAX_DB = 12;
export const BAND_Q = 1.41; // ~1 octave

/** Gentle curves: every boost costs headroom (auto headroom turns the whole signal down by the biggest boost).
 *  Same values as android EqSettings.PRESETS. */
export const PRESETS: Record<string, number[]> = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  Bass: [3, 4.5, 4, 2, 0.5, 0, 0, 0, 0, 0],
  Vocal: [-2, -1.5, -1, 0, 1.5, 2.5, 3, 2, 0.5, 0],
  Treble: [0, 0, 0, 0, 0, 0.5, 1.5, 3, 4, 4],
  Rock: [2.5, 3, 1.5, -0.5, -1.5, -1, 0.5, 2, 3, 2.5],
  Pop: [-1, 0.5, 2, 2.5, 1.5, 0, -0.5, 0, 1, 1.5],
  Classical: [2, 2, 1, 0, 0, 0, 0, 0.5, 1.5, 2],
  Loudness: [3.5, 3, 1, 0, -0.5, -0.5, 0, 1, 2.5, 2.5],
  'Late night': [-3, -2, -1, 0, 0.5, 1, 1, 0, -1, -2],
};
export const PRESET_NAMES = [...Object.keys(PRESETS), 'Custom'];

export type PreampMode = 'headroom' | 'off' | 'boost';

/** An AutoEq headphone correction (parametric filters), applied before the 10 bands when the EQ is on. */
export interface HeadphoneProfile {
  id: string;
  name: string;
  source: string;
  preampDb: number;
  filters: {
    type: 'PK' | 'LSC' | 'HSC';
    fc: number;
    gainDb: number;
    q: number;
  }[];
}

export type ParamType = 'PK' | 'LSC' | 'HSC' | 'LP' | 'HP';

/** One parametric band (same model as android EqBand). Pass filters ignore gain; slope 12 uses q, 24/48 are
 *  Butterworth cascades. */
export interface ParamBand {
  type: ParamType;
  fc: number;
  gainDb: number;
  q: number;
  on: boolean;
  slope: 12 | 24 | 48;
}

export const PARAM_TYPES: ParamType[] = ['PK', 'LSC', 'HSC', 'LP', 'HP'];
export const PARAM_LABELS: Record<ParamType, string> = {
  PK: 'Peak',
  LSC: 'Low shelf',
  HSC: 'High shelf',
  LP: 'Low pass',
  HP: 'High pass',
};
export const PARAM_MAX_GAIN = 15;
export const MAX_PARAM_BANDS = 16;
export const isPass = (b: Pick<ParamBand, 'type'>) => b.type === 'LP' || b.type === 'HP';

export function clampBandParams(b: Partial<ParamBand>): ParamBand {
  const type = PARAM_TYPES.includes(b.type as ParamType) ? (b.type as ParamType) : 'PK';
  const slope = ([12, 24, 48] as const).reduce(
    (a, c) => (Math.abs(c - (b.slope ?? 12)) < Math.abs(a - (b.slope ?? 12)) ? c : a),
    12 as 12 | 24 | 48,
  );
  return {
    type,
    fc: Math.max(10, Math.min(22000, b.fc ?? 1000)),
    gainDb: Math.max(-PARAM_MAX_GAIN, Math.min(PARAM_MAX_GAIN, b.gainDb ?? 0)),
    q: Math.max(0.1, Math.min(20, b.q ?? 1)),
    on: b.on ?? true,
    slope,
  };
}

/** Bands from an AutoEq profile. */
export const bandsFromAutoEq = (p: HeadphoneProfile): ParamBand[] =>
  p.filters.map((f) => clampBandParams({ type: f.type, fc: f.fc, gainDb: f.gainDb, q: f.q })).slice(0, MAX_PARAM_BANDS);

/** Starting point when switching to parametric: the non-flat graphic sliders, as peaks (sounds the same). */
export const bandsFromGraphic = (bands: number[]): ParamBand[] =>
  EQ_FREQS.flatMap((fc, i) => (bands[i] ? [clampBandParams({ type: 'PK', fc, gainDb: bands[i], q: BAND_Q })] : []));

export interface EqSettings {
  enabled: boolean;
  /** 'graphic' = the 10 sliders and presets; 'parametric' = `parametric` bands instead of the sliders. */
  mode: 'graphic' | 'parametric';
  parametric: ParamBand[];
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
    mode: 'graphic',
    parametric: [],
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

/** Trim from the preamp buttons: Quieter / Auto / Louder ('headroom' / 'off' / 'boost' kept as stored names). */
export const PREAMP_MODE_DB: Record<PreampMode, number> = {
  headroom: -3,
  off: 0,
  boost: 3,
};

export function preampGainDb(s: EqSettings, fs = 48000): number {
  if (!s.enabled) return 0;
  return autoHeadroomDb(s, fs) + (PREAMP_MODE_DB[s.preampMode] ?? 0) + s.preampDb;
}

/** Every filter the EQ curve is made of, as RBJ coefficients (Web Audio's peaking / lowshelf use the same formulas). */
export function curveFilters(s: EqSettings, fs = 48000): BiquadCoefficients[] {
  if (!s.enabled) return [];
  const out: BiquadCoefficients[] = [];
  for (const f of s.headphone?.filters ?? []) out.push(rbjCoefficients(f.type, fs, f.fc, f.gainDb, f.q));
  if (s.mode === 'parametric') {
    for (const b of activeParams(s)) out.push(...bandCoefficients(b, fs));
  } else {
    EQ_FREQS.forEach((f, i) => {
      const g = clampBand(s.bands[i] ?? 0);
      if (g !== 0 && f < fs / 2) out.push(rbjCoefficients('PK', fs, f, g, BAND_Q));
    });
  }
  const bass = bassBoostDb(s);
  if (bass > 0) out.push(rbjCoefficients('LSC', fs, BASS_HZ, bass, Math.SQRT1_2));
  return out;
}

/** Butterworth section Qs for 24 / 48 dB/oct pass filters. */
const BUTTERWORTH: Record<number, number[]> = {
  24: [0.5412, 1.3066],
  48: [0.5098, 0.6013, 0.9, 2.5629],
};

/** The Qs of the biquad sections a band becomes (one for peaks/shelves). */
export const sectionQs = (b: ParamBand): number[] => (isPass(b) ? (BUTTERWORTH[b.slope] ?? [b.q]) : [b.q]);

export function bandCoefficients(b: ParamBand, fs: number): BiquadCoefficients[] {
  return sectionQs(b).map((q) => rbjCoefficients(b.type, fs, b.fc, b.gainDb, q));
}

/** Bands that shape the sound: switched on, capped at MAX_PARAM_BANDS. */
const activeParams = (s: EqSettings): ParamBand[] =>
  (s.parametric ?? [])
    .slice(0, MAX_PARAM_BANDS)
    .map(clampBandParams)
    .filter((b) => b.on && (isPass(b) || b.gainDb !== 0));

/** Curve in dB at each frequency (filters only, before headroom), for the response graph. */
export function curveDb(s: EqSettings, freqs: number[], fs = 48000): number[] {
  const filters = curveFilters({ ...s, enabled: true }, fs);
  return freqs.map((f) => filters.reduce((a, c) => a + biquadResponseDb(c, fs, Math.min(f, fs * 0.49)), 0));
}

const clampBand = (v: number) => Math.max(-EQ_MAX_DB, Math.min(EQ_MAX_DB, v));

/** Extra headroom (dB) whenever any filter is active: phase shifts raise the sample peaks of loud masters even where
 * the curve is flat or cut, and the limiter should only catch the odd peak, not work all the time. Same as Android. */
export const SAFETY_DB = 1;

/** Automatic headroom (dB, ≤ 0): minus the curve's highest boost between 20 Hz and 20 kHz, minus [SAFETY_DB]. */
export function autoHeadroomDb(s: EqSettings, fs = 48000): number {
  const filters = curveFilters(s, fs);
  if (!filters.length) return 0;
  const top = Math.min(20000, fs * 0.45);
  const probes = [...EQ_FREQS.filter((f) => f < top), BASS_HZ / 2];
  for (let i = 0; i < 96; i++) probes.push(20 * Math.pow(top / 20, i / 95));
  // Band centres too, so a narrow peak between sweep points isn't missed.
  if (s.mode === 'parametric') for (const b of activeParams(s)) if (b.fc < fs * 0.49) probes.push(b.fc);
  for (const f of s.headphone?.filters ?? []) if (f.fc < fs * 0.49) probes.push(f.fc);
  let peak = -Infinity;
  for (const f of probes) {
    let db = 0;
    for (const c of filters) db += biquadResponseDb(c, fs, f);
    peak = Math.max(peak, db);
  }
  return -(Math.max(0, peak) + SAFETY_DB);
}

/** Bass boost: low shelf at 100 Hz, 0–100 % → 0–12 dB. */
export const BASS_HZ = 100;

export function bassBoostDb(s: EqSettings): number {
  return s.enabled ? (Math.max(0, Math.min(100, s.bassBoost)) / 100) * 12 : 0;
}

export function stereoWidth(s: EqSettings): number {
  if (s.mono) return 0;
  return s.enabled ? 1 + Math.max(0, Math.min(100, s.surround)) / 100 : 1;
}

export function matrixCoefficients(width: number): {
  same: number;
  cross: number;
} {
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
export function rbjCoefficients(type: ParamType, fs: number, fc: number, gainDb: number, q: number): BiquadCoefficients {
  const A = Math.pow(10, gainDb / 40);
  const w0 = (2 * Math.PI * Math.min(fc, fs * 0.49)) / fs;
  const cw = Math.cos(w0);
  const alpha = Math.sin(w0) / (2 * Math.max(0.05, q));
  let b0: number, b1: number, b2: number, a0: number, a1: number, a2: number;
  if (type === 'LP' || type === 'HP') {
    const k = type === 'LP' ? (1 - cw) / 2 : (1 + cw) / 2;
    b0 = k;
    b1 = type === 'LP' ? 1 - cw : -(1 + cw);
    b2 = k;
    a0 = 1 + alpha;
    a1 = -2 * cw;
    a2 = 1 - alpha;
  } else if (type === 'PK') {
    b0 = 1 + alpha * A;
    b1 = -2 * cw;
    b2 = 1 - alpha * A;
    a0 = 1 + alpha / A;
    a1 = -2 * cw;
    a2 = 1 - alpha / A;
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
  createDynamicsCompressor?(): DynamicsCompressorNode;
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
  /** Peak limiter between balance and volume. Starts as a DynamicsCompressor that is only in the chain while the
   * EQ is on; [useLimiter] swaps in the look-ahead worklet, which stays in. Null if unsupported. */
  limiter: AudioNode | null;
  /** Fixed point after the limiter, before volume (the RTA taps it). */
  post: GainNode;
  volume: GainNode;
  /** AutoEq filters currently in the chain (rebuilt when the profile changes). */
  headphone: AudioNode[];
  /** Parametric band nodes currently in the chain, in order. */
  parametric: AudioNode[];
  apply(s: EqSettings): void;
  /** Insert an extra node (e.g. the pitch shifter) between the bass shelf and the stereo matrix. Later inserts go
   * after earlier ones; `output` defaults to `node` (pass it for a sub-graph with separate ends). */
  insert(node: AudioNode, output?: AudioNode): void;
  /** Replace the limiter with [node] (the look-ahead worklet), always in the chain. */
  useLimiter(node: AudioNode): void;
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
  bass.frequency.value = BASS_HZ;
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
  const post = ctx.createGain();
  const volume = ctx.createGain();
  // Fallback limiter until the look-ahead worklet loads: a fast compressor that only acts on peaks near full scale
  // (auto headroom keeps the EQ itself below it).
  const compressor = ctx.createDynamicsCompressor ? ctx.createDynamicsCompressor() : null;
  if (compressor) {
    compressor.threshold.value = -1;
    compressor.knee.value = 0;
    compressor.ratio.value = 20;
    compressor.attack.value = 0.001;
    compressor.release.value = 0.15;
  }
  let limiter: AudioNode | null = compressor;
  let always = false; // the worklet stays in the chain; the compressor only while the EQ is on
  let limiting = false;
  const setLimiter = (on: boolean) => {
    on = on || always;
    if (!limiter || on === limiting) return;
    limiting = on;
    panner.disconnect();
    limiter.disconnect();
    if (on) {
      panner.connect(limiter);
      limiter.connect(post);
    } else panner.connect(post);
  };

  preamp.connect(bands[0]);
  for (let i = 0; i < bands.length - 1; i++) bands[i].connect(bands[i + 1]);
  bands[bands.length - 1].connect(bass);
  bass.connect(splitter);
  merger.connect(panner);
  panner.connect(post);
  post.connect(volume);
  volume.connect(ctx.destination);

  let tail: AudioNode = bass; // last node before the stereo matrix (see insert)
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

  // Parametric bands sit between the graphic bands and the bass shelf. Peaks, Butterworth-ish shelves and pass
  // filters are native BiquadFilterNodes, so dragging them glides smoothly; a shelf with another Q needs an
  // IIRFilterNode (fixed coefficients), rebuilt when it changes.
  const lastBand = bands[bands.length - 1];
  type Slot = { key: string; nodes: AudioNode[]; native: BiquadFilterNode[] };
  let slots: Slot[] = [];
  const nativeShelf = (b: ParamBand) => !ctx.createIIRFilter || Math.abs(b.q - Math.SQRT1_2) < 0.02;
  const slotKey = (b: ParamBand) => {
    if (isPass(b)) return `${b.type}/${b.slope}/${b.on}`;
    if (b.type === 'PK' || nativeShelf(b)) return `${b.type}/native`;
    return `${b.type}/iir/${b.fc}/${b.q}/${b.on ? b.gainDb : 0}`;
  };
  const nativeType: Record<ParamType, BiquadFilterType> = {
    PK: 'peaking',
    LSC: 'lowshelf',
    HSC: 'highshelf',
    LP: 'lowpass',
    HP: 'highpass',
  };
  const makeSlot = (b: ParamBand, key: string): Slot => {
    if (key.includes('/iir/')) {
      const fs = ctx.sampleRate ?? 48000;
      const c = rbjCoefficients(b.type, fs, b.fc, b.on ? b.gainDb : 0, b.q);
      return { key, nodes: [ctx.createIIRFilter!(c.b, c.a)], native: [] };
    }
    const n = isPass(b) && !b.on ? 0 : sectionQs(b).length;
    const native = Array.from({ length: n }, () => {
      const f = ctx.createBiquadFilter();
      f.type = nativeType[b.type];
      f.frequency.value = b.fc;
      f.gain.value = 0; // peaks/shelves glide in from flat
      return f;
    });
    return { key, nodes: native, native };
  };
  const setParametric = (s: EqSettings) => {
    const want = s.enabled && s.mode === 'parametric' ? (s.parametric ?? []).slice(0, MAX_PARAM_BANDS).map(clampBandParams) : [];
    const keys = want.map(slotKey);
    if (keys.length !== slots.length || keys.some((k, i) => k !== slots[i].key)) {
      lastBand.disconnect();
      for (const sl of slots) for (const n of sl.nodes) n.disconnect();
      // Keep nodes (and their filter state) where the band at that index kept its shape.
      slots = want.map((b, i) => (slots[i]?.key === keys[i] ? slots[i] : makeSlot(b, keys[i])));
      let prev: AudioNode = lastBand;
      for (const sl of slots)
        for (const n of sl.nodes) {
          prev.connect(n);
          prev = n;
        }
      prev.connect(bass);
    }
    const t = ctx.currentTime;
    want.forEach((b, i) => {
      const qs = sectionQs(b);
      slots[i].native.forEach((f, j) => {
        setParam(f.frequency, b.fc, t);
        // Web Audio reads lowpass/highpass Q in dB.
        setParam(f.Q, isPass(b) ? 20 * Math.log10(qs[j]) : b.q, t);
        if (!isPass(b)) setParam(f.gain, b.on ? b.gainDb : 0, t);
      });
    });
    graph.parametric = slots.flatMap((sl) => sl.nodes);
  };

  const graph: EqGraph = {
    input: preamp,
    headphone: [],
    parametric: [],
    output: volume,
    preamp,
    bands,
    bass,
    matrix: { ll, lr, rl, rr },
    panner,
    limiter,
    post,
    volume,
    apply(s: EqSettings) {
      const t = ctx.currentTime;
      setHeadphone(s);
      setParametric(s);
      setLimiter(s.enabled);
      setParam(preamp.gain, dbToGain(preampGainDb(s, ctx.sampleRate ?? 48000)), t);
      const graphic = s.enabled && s.mode !== 'parametric';
      bands.forEach((b, i) => setParam(b.gain, graphic ? clampBand(s.bands[i] ?? 0) : 0, t));
      setParam(bass.gain, bassBoostDb(s), t);
      const { same, cross } = matrixCoefficients(stereoWidth(s));
      setParam(ll.gain, same, t);
      setParam(rr.gain, same, t);
      setParam(lr.gain, cross, t);
      setParam(rl.gain, cross, t);
      setParam(panner.pan, Math.max(-1, Math.min(1, s.balance)), t);
      setParam(volume.gain, Math.max(0, Math.min(1, s.volume)), t);
    },
    insert(node: AudioNode, output: AudioNode = node) {
      tail.disconnect();
      tail.connect(node);
      output.connect(splitter);
      tail = output;
    },
    useLimiter(node: AudioNode) {
      setLimiter(false);
      limiter?.disconnect();
      limiter = node;
      graph.limiter = node;
      always = true;
      limiting = false;
      setLimiter(true);
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
/** RTA band centres: 1/6 octave, 20 Hz – 20 kHz (as on Android). */
export const RTA_CENTRES = Array.from({ length: 61 }, (_, i) => 20 * Math.pow(2, i / 6)).filter((f) => f <= 20500);

/** Web Audio's analyser reads a full-scale sine about 12 dB low (Blackman window, 1/N scaling); this lifts it to 0 dB. */
const RTA_OFFSET_DB = 12;

/**
 * RTA levels per [RTA_CENTRES] band from AnalyserNode.getFloatFrequencyData (dB per bin): power summed over each
 * band, or the 3 bins round its centre where the band is narrower than that. Floor −120 dB.
 */
export function rtaBands(binsDb: Float32Array | number[], fs: number, out: number[] = []): number[] {
  const n = binsDb.length; // fftSize / 2
  const binHz = fs / (2 * n);
  const edge = Math.pow(2, 1 / 12);
  RTA_CENTRES.forEach((c, b) => {
    let k0 = Math.max(1, Math.ceil(c / edge / binHz));
    let k1 = Math.min(n - 1, Math.floor((c * edge) / binHz));
    if (k1 - k0 < 2) {
      const kc = Math.max(2, Math.min(n - 2, Math.round(c / binHz)));
      k0 = kc - 1;
      k1 = kc + 1;
    }
    let p = 0;
    for (let k = k0; k <= k1; k++) {
      const v = binsDb[k];
      if (Number.isFinite(v)) p += Math.pow(10, v / 10);
    }
    out[b] = c >= fs / 2 || p <= 0 ? -120 : Math.max(-120, 10 * Math.log10(p) + RTA_OFFSET_DB);
  });
  return out;
}

/** Look-ahead peak limiter (same algorithm as Android's LookaheadLimiter). */
export const LIMITER_WORKLET_URL = 'limiter-worklet.js';

export const semitonesToRatio = (st: number) => Math.pow(2, st / 12);
