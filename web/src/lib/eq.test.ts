import { describe, expect, it } from 'vitest';
import {
  EQ_FREQS,
  balanceLabel,
  buildEqGraph,
  dbToGain,
  defaultEq,
  loadProfiles,
  autoHeadroomDb,
  bandsFromAutoEq,
  bandsFromGraphic,
  clampBandParams,
  curveDb,
  biquadResponseDb,
  curveFilters,
  matrixCoefficients,
  presetFor,
  PRESETS,
  RTA_CENTRES,
  SAFETY_DB,
  rtaBands,
  saveProfiles,
  type AudioCtxLike,
} from './eq';

// Minimal fake of the Web Audio node API: records connections and param values.
class FakeParam {
  value = 0;
}
class FakeNode {
  out: { node: FakeNode; output?: number; input?: number }[] = [];
  constructor(public kind: string) {}
  connect(node: FakeNode, output?: number, input?: number) {
    this.out.push({ node, output, input });
    return node;
  }
  disconnect() {
    this.out = [];
  }
}
class FakeGain extends FakeNode {
  gain = new FakeParam();
  constructor() {
    super('gain');
    this.gain.value = 1;
  }
}
class FakeBiquad extends FakeNode {
  type = 'lowpass';
  frequency = new FakeParam();
  Q = new FakeParam();
  gain = new FakeParam();
  constructor() {
    super('biquad');
  }
}
class FakePanner extends FakeNode {
  pan = new FakeParam();
  constructor() {
    super('panner');
  }
}
function fakeCtx() {
  const destination = new FakeNode('destination');
  const ctx = {
    destination,
    currentTime: 0,
    createGain: () => new FakeGain(),
    createBiquadFilter: () => new FakeBiquad(),
    createStereoPanner: () => new FakePanner(),
    createChannelSplitter: () => new FakeNode('splitter'),
    createChannelMerger: () => new FakeNode('merger'),
  };
  return ctx as unknown as AudioCtxLike & { destination: FakeNode };
}

/** Follow the first connection from `start` to the destination, returning node kinds. */
function chain(start: FakeNode): string[] {
  const seen: string[] = [];
  let n: FakeNode | undefined = start;
  while (n && seen.length < 40) {
    seen.push(n.kind);
    n = n.out[0]?.node;
  }
  return seen;
}

describe('buildEqGraph', () => {
  it('wires preamp → 10 peaking bands → bass shelf → stereo matrix → panner → volume → destination', () => {
    const ctx = fakeCtx();
    const g = buildEqGraph(ctx);
    expect(g.bands).toHaveLength(10);
    g.bands.forEach((b, i) => {
      expect(b.type).toBe('peaking');
      expect(b.frequency.value).toBe(EQ_FREQS[i]);
    });
    expect(g.bass.type).toBe('lowshelf');
    expect(chain(g.input as unknown as FakeNode)).toEqual([
      'gain',
      ...Array(10).fill('biquad'),
      'biquad',
      'splitter',
      'gain',
      'merger',
      'panner',
      'gain',
      'gain',
      'destination',
    ]);
    // The matrix routes both channels to both outputs.
    const m = g.matrix as unknown as Record<string, FakeNode>;
    expect(m.ll.out[0].input).toBe(0);
    expect(m.rl.out[0].input).toBe(0);
    expect(m.rr.out[0].input).toBe(1);
    expect(m.lr.out[0].input).toBe(1);
  });

  it('applies bands, preamp, bass boost, balance and volume when enabled', () => {
    const g = buildEqGraph(fakeCtx());
    const s = {
      ...defaultEq(),
      enabled: true,
      bands: [...PRESETS.Rock],
      preampMode: 'headroom' as const,
      preampDb: 1,
      bassBoost: 50,
      balance: -0.5,
      volume: 0.4,
    };
    g.apply(s);
    expect(g.bands.map((b) => b.gain.value)).toEqual(PRESETS.Rock);
    // auto headroom + Quieter (−3) + 1 dB trim
    expect(autoHeadroomDb(s)).toBeLessThan(-6);
    expect(g.preamp.gain.value).toBeCloseTo(dbToGain(autoHeadroomDb(s) - 2));
    expect(g.bass.gain.value).toBe(6);
    expect(g.panner.pan.value).toBe(-0.5);
    expect(g.volume.gain.value).toBe(0.4);
  });

  it('is neutral when disabled but keeps volume', () => {
    const g = buildEqGraph(fakeCtx());
    g.apply({
      ...defaultEq(),
      enabled: false,
      bands: [...PRESETS.Bass],
      bassBoost: 100,
      preampMode: 'boost',
      volume: 0.7,
    });
    expect(g.bands.every((b) => b.gain.value === 0)).toBe(true);
    expect(g.bass.gain.value).toBe(0);
    expect(g.preamp.gain.value).toBe(1);
    expect(g.matrix.ll.gain.value).toBe(1);
    expect(g.matrix.lr.gain.value).toBe(0);
    expect(g.volume.gain.value).toBe(0.7);
  });

  it('mixes to mono and widens', () => {
    const g = buildEqGraph(fakeCtx());
    g.apply({ ...defaultEq(), mono: true });
    expect(g.matrix.ll.gain.value).toBe(0.5);
    expect(g.matrix.lr.gain.value).toBe(0.5);
    g.apply({ ...defaultEq(), enabled: true, surround: 100 });
    expect(matrixCoefficients(2)).toEqual({ same: 1.5, cross: -0.5 });
    expect(g.matrix.rr.gain.value).toBe(1.5);
    expect(g.matrix.rl.gain.value).toBe(-0.5);
  });

  it('clamps bands to ±12 dB', () => {
    const g = buildEqGraph(fakeCtx());
    g.apply({
      ...defaultEq(),
      enabled: true,
      bands: [20, -20, 0, 0, 0, 0, 0, 0, 0, 0],
    });
    expect(g.bands[0].gain.value).toBe(12);
    expect(g.bands[1].gain.value).toBe(-12);
  });

  it('can insert a node before the stereo matrix', () => {
    const g = buildEqGraph(fakeCtx());
    const extra = new FakeNode('pitch');
    g.insert(extra as unknown as AudioNode);
    expect(chain(g.bass as unknown as FakeNode).slice(0, 3)).toEqual(['biquad', 'pitch', 'splitter']);
  });
});

describe('auto headroom', () => {
  it('takes off the biggest boost of the whole curve plus a safety margin, and only the margin for cuts', () => {
    const one = {
      ...defaultEq(),
      enabled: true,
      bands: [0, 0, 0, 0, 0, 12, 0, 0, 0, 0],
    };
    expect(autoHeadroomDb(one)).toBeCloseTo(-12 - SAFETY_DB, 1);
    expect(autoHeadroomDb({ ...one, bands: Array(10).fill(-6) })).toBe(-SAFETY_DB);
    expect(autoHeadroomDb({ ...one, bands: Array(10).fill(0) })).toBe(0);
    expect(autoHeadroomDb({ ...one, enabled: false })).toBe(0);
    // Bass boost stacks with the low bands.
    const stacked = autoHeadroomDb({
      ...one,
      bands: [6, 6, 0, 0, 0, 0, 0, 0, 0, 0],
      bassBoost: 100,
    });
    expect(stacked).toBeLessThan(-16);
    // With it applied, nothing in the audible range is above 0 dB.
    const s = {
      ...defaultEq(),
      enabled: true,
      bands: [...PRESETS.Rock],
      bassBoost: 40,
    };
    for (let f = 20; f < 20000; f *= 1.05) {
      const db = curveFilters(s).reduce((a, c) => a + biquadResponseDb(c, 48000, f), 0) + autoHeadroomDb(s);
      expect(db).toBeLessThan(0.05);
    }
  });

  it('keeps presets gentle', () => {
    for (const [name, bands] of Object.entries(PRESETS)) {
      expect(autoHeadroomDb({ ...defaultEq(), enabled: true, bands }), name).toBeGreaterThan(-7);
    }
  });

  it('puts a limiter in the chain only while the EQ is on', () => {
    const ctx = Object.assign(fakeCtx(), {
      createDynamicsCompressor: () =>
        Object.assign(new FakeNode('limiter'), {
          threshold: new FakeParam(),
          knee: new FakeParam(),
          ratio: new FakeParam(),
          attack: new FakeParam(),
          release: new FakeParam(),
        }),
    });
    const g = buildEqGraph(ctx as unknown as AudioCtxLike);
    g.apply({ ...defaultEq(), enabled: true });
    expect(chain(g.panner as unknown as FakeNode)).toEqual(['panner', 'limiter', 'gain', 'gain', 'destination']);
    g.apply({ ...defaultEq(), enabled: false });
    expect(chain(g.panner as unknown as FakeNode)).toEqual(['panner', 'gain', 'gain', 'destination']);
    // The look-ahead worklet replaces it and stays in, EQ on or off.
    g.useLimiter(new FakeNode('worklet') as unknown as AudioNode);
    expect(chain(g.panner as unknown as FakeNode)).toEqual(['panner', 'worklet', 'gain', 'gain', 'destination']);
    g.apply({ ...defaultEq(), enabled: true });
    expect(chain(g.panner as unknown as FakeNode)).toEqual(['panner', 'worklet', 'gain', 'gain', 'destination']);
  });

  it('catches a narrow peak between the sweep points', () => {
    const s = {
      ...defaultEq(),
      enabled: true,
      mode: 'parametric' as const,
      parametric: [{ type: 'PK' as const, fc: 3170, gainDb: 12, q: 20, on: true, slope: 12 as const }],
    };
    expect(autoHeadroomDb(s)).toBeCloseTo(-12 - SAFETY_DB, 1);
  });
});

it('reads the RTA level of a tone in its own band', () => {
  // A 1 kHz tone as Web Audio's analyser reports it: main lobe over 3 bins, the rest at the floor.
  const fs = 48000;
  const n = 4096; // fftSize 8192
  const bins = new Float32Array(n).fill(-200);
  const k = Math.round(1000 / (fs / 2 / n));
  bins[k - 1] = -24;
  bins[k] = -18; // about what a −6 dBFS sine reads (the analyser reads ≈ 12 dB low)
  bins[k + 1] = -24;
  const db = rtaBands(bins, fs);
  const i = RTA_CENTRES.findIndex((c) => Math.abs(c - 1008) < 10);
  expect(db[i]).toBeGreaterThan(-6);
  expect(db[i]).toBeLessThan(0);
  expect(db[i + 6]).toBe(-120);
  expect(db.length).toBe(RTA_CENTRES.length);
});

it('detects presets', () => {
  expect(presetFor([...PRESETS.Pop])).toBe('Pop');
  expect(presetFor([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])).toBe('Custom');
});

it('formats balance like the reference', () => {
  expect(balanceLabel(0)).toBe('Left:100, Right:100');
  expect(balanceLabel(1)).toBe('Left:0, Right:100');
  expect(balanceLabel(-0.25)).toBe('Left:100, Right:75');
});

it('stores per-output profiles', () => {
  const mem = new Map<string, string>();
  const storage = {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, v),
  };
  saveProfiles(storage, {
    default: { ...defaultEq(), enabled: true },
    'moto buds': { ...defaultEq(), bassBoost: 31 },
  });
  const p = loadProfiles(storage);
  expect(p.default.enabled).toBe(true);
  expect(p['moto buds'].bassBoost).toBe(31);
  expect(loadProfiles({ getItem: () => '{bad' })).toEqual({});
});

describe('AutoEq parametric filters', () => {
  it('RBJ shelves and peaks have the requested gain', async () => {
    const { rbjCoefficients, biquadResponseDb } = await import('./eq');
    const fs = 48000;
    const pk = rbjCoefficients('PK', fs, 1000, 6, 1.4);
    expect(biquadResponseDb(pk, fs, 1000)).toBeCloseTo(6, 3);
    expect(Math.abs(biquadResponseDb(pk, fs, 50))).toBeLessThan(0.1);
    const ls = rbjCoefficients('LSC', fs, 105, 6.4, 0.7);
    expect(biquadResponseDb(ls, fs, 10)).toBeCloseTo(6.4, 1);
    expect(Math.abs(biquadResponseDb(ls, fs, 5000))).toBeLessThan(0.05);
    expect(biquadResponseDb(ls, fs, 105)).toBeCloseTo(3.2, 1); // half the gain at fc
    const hs = rbjCoefficients('HSC', fs, 10000, -2.1, 0.7);
    expect(biquadResponseDb(hs, fs, 23000)).toBeCloseTo(-2.1, 1);
    expect(Math.abs(biquadResponseDb(hs, fs, 100))).toBeLessThan(0.01);
  });

  it('inserts IIR filters between the preamp and the bands, and removes them', () => {
    const ctx = Object.assign(fakeCtx(), {
      createIIRFilter: () => new FakeNode('iir'),
      sampleRate: 48000,
    });
    const g = buildEqGraph(ctx as unknown as AudioCtxLike);
    const hp = {
      id: 'x',
      name: 'HD 650',
      source: 'oratory1990',
      preampDb: -6,
      filters: [
        { type: 'LSC' as const, fc: 105, gainDb: 6.4, q: 0.7 },
        { type: 'PK' as const, fc: 8800, gainDb: 5.1, q: 1.42 },
      ],
    };
    g.apply({ ...defaultEq(), enabled: true, headphone: hp });
    expect(chain(g.preamp as unknown as FakeNode).slice(0, 4)).toEqual(['gain', 'iir', 'iir', 'biquad']);
    // Auto headroom covers the profile's boosts (≈ its own −6 dB preamp).
    expect((g.preamp as unknown as FakeGain).gain.value).toBeCloseTo(
      dbToGain(autoHeadroomDb({ ...defaultEq(), enabled: true, headphone: hp })),
    );
    expect(autoHeadroomDb({ ...defaultEq(), enabled: true, headphone: hp })).toBeLessThan(-6);
    g.apply({ ...defaultEq(), enabled: false, headphone: hp });
    expect(chain(g.preamp as unknown as FakeNode).slice(0, 2)).toEqual(['gain', 'biquad']);
  });
});

describe('parametric EQ', () => {
  const para = (...b: Partial<import('./eq').ParamBand>[]) => ({
    ...defaultEq(),
    enabled: true,
    mode: 'parametric' as const,
    parametric: b.map(clampBandParams),
  });

  it('replaces the graphic sliders and puts native filters before the bass shelf', () => {
    const ctx = Object.assign(fakeCtx(), { sampleRate: 48000 });
    const g = buildEqGraph(ctx);
    g.apply({
      ...para({ type: 'PK', fc: 2000, gainDb: -6, q: 2 }, { type: 'HP', fc: 40, slope: 24 }),
      bands: Array(10).fill(6),
    });
    expect(g.bands.every((b) => b.gain.value === 0)).toBe(true);
    expect(chain(g.bands[9] as unknown as FakeNode).slice(0, 5)).toEqual(['biquad', 'biquad', 'biquad', 'biquad', 'biquad']);
    const [pk, hp1, hp2] = g.parametric as unknown as FakeBiquad[];
    expect([pk.type, pk.frequency.value, pk.gain.value, pk.Q.value]).toEqual(['peaking', 2000, -6, 2]);
    expect(hp1.type).toBe('highpass');
    expect(hp2.Q.value).toBeCloseTo(20 * Math.log10(1.3066)); // Web Audio wants dB
    expect(chain(hp2 as unknown as FakeNode)[1]).toBe('biquad'); // → bass shelf
    expect(hp2.out[0].node).toBe(g.bass);
    // Back to graphic: the parametric nodes leave the chain.
    g.apply({ ...para(), mode: 'graphic', bands: Array(10).fill(6) });
    expect(g.parametric).toHaveLength(0);
    expect((g.bands[9] as unknown as FakeNode).out[0].node).toBe(g.bass);
    expect(g.bands[0].gain.value).toBe(6);
  });

  it('keeps nodes when only values change', () => {
    const g = buildEqGraph(fakeCtx());
    g.apply(para({ type: 'PK', fc: 1000, gainDb: 3 }));
    const before = g.parametric[0];
    g.apply(para({ type: 'PK', fc: 1200, gainDb: 5 }));
    expect(g.parametric[0]).toBe(before);
    expect((before as unknown as FakeBiquad).frequency.value).toBe(1200);
  });

  it('draws the right curve: slopes, resonance and headroom', () => {
    const lp = curveDb(para({ type: 'LP', fc: 200, slope: 24 }), [200, 1600, 3200]);
    expect(lp[0]).toBeCloseTo(-3, 0);
    expect(lp[2] - lp[1]).toBeCloseTo(-24, 0);
    const hp12 = curveDb(para({ type: 'HP', fc: 200, q: Math.SQRT1_2 }), [25, 50]);
    expect(hp12[0] - hp12[1]).toBeCloseTo(-12, 0);
    expect(autoHeadroomDb(para({ type: 'LP', fc: 5000, q: 4 }))).toBeCloseTo(-20 * Math.log10(4) - SAFETY_DB, 0);
    expect(autoHeadroomDb(para({ type: 'HP', fc: 80, slope: 48 }))).toBeCloseTo(-SAFETY_DB, 1);
    expect(autoHeadroomDb(para({ type: 'PK', fc: 1000, gainDb: 6, on: false }))).toBe(0);
  });

  it('imports from AutoEq and the sliders, and clamps', () => {
    expect(
      bandsFromAutoEq({
        id: 'x',
        name: 'n',
        source: 's',
        preampDb: -6,
        filters: [{ type: 'LSC', fc: 105, gainDb: 6.4, q: 0.7 }],
      })[0],
    ).toEqual({
      type: 'LSC',
      fc: 105,
      gainDb: 6.4,
      q: 0.7,
      on: true,
      slope: 12,
    });
    expect(bandsFromGraphic([0, 3, 0, 0, 0, -2, 0, 0, 0, 0]).map((b) => b.fc)).toEqual([63, 1000]);
    expect(
      clampBandParams({
        type: 'XX' as never,
        fc: 5,
        gainDb: 99,
        q: 0,
        slope: 30 as never,
      }),
    ).toEqual({ type: 'PK', fc: 10, gainDb: 15, q: 0.1, on: true, slope: 24 });
  });
});
