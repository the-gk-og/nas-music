import { describe, expect, it } from 'vitest';
import {
  EQ_FREQS,
  balanceLabel,
  buildEqGraph,
  dbToGain,
  defaultEq,
  loadProfiles,
  matrixCoefficients,
  presetFor,
  PRESETS,
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
    g.apply({ ...defaultEq(), enabled: true, bands: [...PRESETS.Rock], preampMode: 'headroom', preampDb: 1, bassBoost: 50, balance: -0.5, volume: 0.4 });
    expect(g.bands.map((b) => b.gain.value)).toEqual(PRESETS.Rock);
    expect(g.preamp.gain.value).toBeCloseTo(dbToGain(-5));
    expect(g.bass.gain.value).toBe(6);
    expect(g.panner.pan.value).toBe(-0.5);
    expect(g.volume.gain.value).toBe(0.4);
  });

  it('is neutral when disabled but keeps volume', () => {
    const g = buildEqGraph(fakeCtx());
    g.apply({ ...defaultEq(), enabled: false, bands: [...PRESETS.Bass], bassBoost: 100, preampMode: 'boost', volume: 0.7 });
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
    g.apply({ ...defaultEq(), enabled: true, bands: [20, -20, 0, 0, 0, 0, 0, 0, 0, 0] });
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
  const storage = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) };
  saveProfiles(storage, { default: { ...defaultEq(), enabled: true }, 'moto buds': { ...defaultEq(), bassBoost: 31 } });
  const p = loadProfiles(storage);
  expect(p.default.enabled).toBe(true);
  expect(p['moto buds'].bassBoost).toBe(31);
  expect(loadProfiles({ getItem: () => '{bad' })).toEqual({});
});
