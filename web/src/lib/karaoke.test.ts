import { describe, expect, it } from 'vitest';
import type { AudioCtxLike } from './eq';
import { buildEqGraph } from './eq';
import { buildKaraoke, karaokeSample, KARAOKE_KEEP } from './karaoke';

// A tiny static evaluator for the node graph: gains multiply, low-passes pass a constant (DC) signal fully
// and a high tone not at all, splitters/mergers route channels. Enough to check the wiring matches the maths.
type Edge = { node: N; output?: number; input?: number };
class N {
  out: Edge[] = [];
  gain = { value: 1 };
  frequency = { value: 0 };
  Q = { value: 0 };
  type = '';
  constructor(public kind: string) {}
  connect(node: N, output?: number, input?: number) {
    this.out.push({ node, output, input });
    return node;
  }
  disconnect() {
    this.out = [];
  }
}
function ctx(): AudioCtxLike {
  return {
    destination: new N('destination'),
    currentTime: 0,
    createGain: () => new N('gain'),
    createBiquadFilter: () => new N('biquad'),
    createStereoPanner: () => new N('panner'),
    createChannelSplitter: () => new N('splitter'),
    createChannelMerger: () => new N('merger'),
  } as unknown as AudioCtxLike;
}

function run(input: N, output: N, l: number, r: number, lowPassGain: number): number[] {
  const incoming = new Map<N, { from: N; e: Edge }[]>();
  const seen = new Set<N>();
  const walk = (n: N) => {
    if (seen.has(n)) return;
    seen.add(n);
    for (const e of n.out) {
      incoming.set(e.node, [...(incoming.get(e.node) ?? []), { from: n, e }]);
      walk(e.node);
    }
  };
  walk(input);
  const memo = new Map<N, number[]>();
  const value = (n: N): number[] => {
    if (n === input) return [l, r];
    const hit = memo.get(n);
    if (hit) return hit;
    let acc: number[] = [];
    const add = (sig: number[]) => {
      if (acc.length < sig.length) acc = sig.map((_, i) => acc[i] ?? acc[0] ?? 0);
      acc = acc.map((a, i) => a + (sig[i] ?? sig[0]));
    };
    if (n.kind === 'merger') {
      acc = [0, 0];
      for (const { from, e } of incoming.get(n) ?? []) {
        const v = value(from);
        acc[e.input ?? 0] += from.kind === 'splitter' ? v[e.output ?? 0] : v[0];
      }
    } else {
      for (const { from, e } of incoming.get(n) ?? []) {
        const v = value(from);
        add(from.kind === 'splitter' ? [v[e.output ?? 0]] : v);
      }
    }
    if (n.kind === 'gain') acc = acc.map((x) => x * n.gain.value);
    if (n.kind === 'biquad') acc = acc.map((x) => x * lowPassGain);
    memo.set(n, acc);
    return acc;
  };
  return value(output);
}

describe('karaoke', () => {
  const cases: [number, number][] = [
    [1, 1], // dead centre (the vocal)
    [1, 0], // hard left
    [0.3, -0.7],
  ];

  it('matches the reference maths for bass (DC) and for a high tone', () => {
    for (const lp of [1, 0]) {
      for (const [l, r] of cases) {
        const k = buildKaraoke(ctx());
        k.set(true);
        const out = run(k.input as unknown as N, k.output as unknown as N, l, r, lp);
        const want = karaokeSample(l, r, lp * ((l + r) / 2));
        expect(out[0]).toBeCloseTo(want[0], 9);
        expect(out[1]).toBeCloseTo(want[1], 9);
      }
    }
  });

  it('turns a centred high tone down to KARAOKE_KEEP and leaves the bass and the sides', () => {
    const k = buildKaraoke(ctx());
    k.set(true);
    const io = [k.input as unknown as N, k.output as unknown as N] as const;
    expect(run(...io, 1, 1, 0)).toEqual([KARAOKE_KEEP, KARAOKE_KEEP].map((x) => expect.closeTo(x, 9)));
    expect(run(...io, 1, 1, 1).map((x) => +x.toFixed(9))).toEqual([1, 1]);
    expect(run(...io, 1, -1, 0).map((x) => +x.toFixed(9))).toEqual([1, -1]);
  });

  it('is a clean pass-through when off', () => {
    const k = buildKaraoke(ctx());
    k.set(false);
    expect(run(k.input as unknown as N, k.output as unknown as N, 0.4, -0.2, 0)).toEqual([0.4, -0.2]);
  });

  it('can be inserted into the EQ graph ahead of other inserts', () => {
    const c = ctx();
    const g = buildEqGraph(c);
    const k = buildKaraoke(c);
    g.insert(k.input, k.output);
    const pitch = new N('pitch');
    g.insert(pitch as unknown as AudioNode);
    expect((g.bass as unknown as N).out.map((e) => e.node)).toEqual([k.input]);
    expect((k.output as unknown as N).out.map((e) => e.node)).toEqual([pitch]);
    expect(pitch.out[0].node.kind).toBe('splitter');
  });
});
