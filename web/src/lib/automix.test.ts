import { describe, expect, it } from 'vitest';
import {
  BASS_KILL_DB,
  bassIn,
  bassOut,
  correctedRate,
  djBeats,
  expectedIn,
  planTransition,
  progress,
  rampRate,
  RAMP_BEATS,
  snapBar,
  tempoMatch,
  volIn,
  volOut,
  type DjOptions,
} from './automix';
import { crossfadeFor, defaultListening, djFor } from './listening';
import type { Track, TrackMix } from './types';

const mix = (bpm: number, extra: Partial<TrackMix> = {}): TrackMix => ({
  bpm, beatS: 0.5, downbeatS: 0.5, beatsPerBar: 4, gridStable: true, mixInS: 0.5, mixOutS: 0.5 + (60 / bpm) * 4 * 96, lastBeatS: 0.5 + (60 / bpm) * 4 * 112, ...extra,
});
const tr = (id: string, m: TrackMix | null, durationS = 240, albumId = id): Track => ({
  id, title: id, artistId: 'ar', artist: 'A', albumId, album: albumId, trackNo: 1, durationS, starred: false, mix: m,
});
const opts: DjOptions = { beats: 16, allowTempo: true, fallbackS: 6 };
const near = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) < eps;

describe('tempo matching', () => {
  it('picks the smallest stretch, incl. half/double time', () => {
    expect(tempoMatch(128, 126)).toEqual({ multiplier: 1, rate: 128 / 126 });
    expect(tempoMatch(140, 70.5).multiplier).toBe(2);
    expect(tempoMatch(87, 172).multiplier).toBe(0.5);
  });
  it('snaps to bars', () => {
    expect(snapBar(10.3, 0.5, 2)).toBe(10.5);
    expect(snapBar(10.3, 0.5, 2, 'floor')).toBe(8.5);
    expect(snapBar(10.5, 0.5, 2, 'ceil')).toBe(10.5);
    expect(snapBar(-3, 0.5, 2, 'floor')).toBe(0.5);
    expect(djBeats(20)).toBe(16);
    expect(djBeats(30)).toBe(32);
    expect(djBeats('x')).toBe(16);
  });
});

describe('planTransition', () => {
  // 124 BPM: beat 0.4839 s, bar 1.935 s. 120 bars = 232 s; mixOut at bar 96, last beat at bar 112.
  const a = tr('a', mix(124));
  const b = tr('b', mix(126, { mixInS: 2.0, downbeatS: 0.1 }));

  it('beat-matches: starts at the mix-out bar, stretches the incoming tempo, aligns downbeats', () => {
    const p = planTransition(a, b, opts)!;
    expect(p.beatmatch).toBe(true);
    expect(near(p.outStartS, a.mix!.mixOutS!)).toBe(true);
    expect(p.beats).toBe(16);
    expect(near(p.durationS, 16 * 60 / 124)).toBe(true);
    expect(near(p.rate, 124 / 126)).toBe(true);
    // incoming starts on one of its own downbeats (0.1 + n * bar)
    const inBar = (60 / 126) * 4;
    const n = (p.inStartS - 0.1) / inBar;
    expect(near(n, Math.round(n), 1e-9)).toBe(true);
    // after k outgoing beats, the incoming has advanced exactly k of its own beats
    const k = 8;
    const outPos = p.outStartS + k * 60 / 124;
    expect(near(expectedIn(p, outPos) - p.inStartS, k * 60 / 126, 1e-9)).toBe(true);
  });

  it('moves the start earlier (on a bar line) when the outro is too short, and ends by the last beat', () => {
    const last = 0.5 + (60 / 124) * 4 * 100;
    const short = tr('s', mix(124, { lastBeatS: last }));
    const p = planTransition(short, b, { ...opts, beats: 32 })!;
    expect(p.outStartS + p.durationS).toBeLessThanOrEqual(last + 1e-6);
    const bars = (p.outStartS - 0.5) / ((60 / 124) * 4);
    expect(near(bars, Math.round(bars), 1e-9)).toBe(true);
    expect(p.beats).toBe(32);
  });

  it('half/double time', () => {
    const p = planTransition(tr('a', mix(140)), tr('b', mix(71)), opts)!;
    expect(p.beatmatch).toBe(true);
    expect(p.multiplier).toBe(2);
    expect(near(p.rate, 140 / 142)).toBe(true);
  });

  it('falls back to a crossfade between mix points when tempos are too far apart', () => {
    const p = planTransition(a, tr('b', mix(100, { mixInS: 3 })), opts)!;
    expect(p.beatmatch).toBe(false);
    expect(p.rate).toBe(1);
    expect(p.durationS).toBe(6);
    expect(p.inStartS).toBe(3);
    expect(near(p.outStartS, a.mix!.mixOutS!)).toBe(true);
  });

  it('allow tempo change off: only near-identical tempos are beat-matched', () => {
    expect(planTransition(a, b, { ...opts, allowTempo: false })!.beatmatch).toBe(false);
    const same = planTransition(a, tr('b', mix(124.2)), { ...opts, allowTempo: false })!;
    expect(same.beatmatch).toBe(true);
  });

  it('unstable grid: no beat-matching; no analysis: null (normal crossfade)', () => {
    expect(planTransition(a, tr('b', mix(124, { gridStable: false })), opts)!.beatmatch).toBe(false);
    expect(planTransition(a, tr('b', null), opts)).toBeNull();
    expect(planTransition(a, a, opts)).toBeNull();
  });

  it('never starts in the first half of the outgoing track', () => {
    const early = tr('e', mix(124, { mixOutS: 30, lastBeatS: 60 }), 62);
    const p = planTransition(early, b, opts);
    if (p) expect(p.outStartS).toBeGreaterThanOrEqual(31);
  });
});

describe('curves', () => {
  const p = planTransition(tr('a', mix(124)), tr('b', mix(124)), opts)!;
  it('DJ volumes: incoming full by the middle, outgoing full until the middle', () => {
    expect(volIn(p, 0)).toBe(0);
    expect(near(volIn(p, 0.5), 1)).toBe(true);
    expect(near(volOut(p, 0.5), 1)).toBe(true);
    expect(near(volOut(p, 1), 0)).toBe(true);
  });
  it('bass swap at the middle, over one beat, mirror images', () => {
    expect(bassIn(p, 0)).toBe(BASS_KILL_DB);
    expect(bassIn(p, 1)).toBe(0);
    expect(bassOut(p, 0)).toBe(0);
    expect(bassOut(p, 1)).toBe(BASS_KILL_DB);
    const beat = 1 / p.beats;
    expect(bassIn(p, 0.5 - beat)).toBe(BASS_KILL_DB);
    expect(bassIn(p, 0.5 + beat)).toBe(0);
    expect(near(bassIn(p, 0.5), BASS_KILL_DB / 2)).toBe(true);
    for (const x of [0, 0.3, 0.5, 0.52, 1]) expect(near(bassIn(p, x) + bassOut(p, x), BASS_KILL_DB)).toBe(true);
  });
  it('progress', () => {
    expect(progress(p, p.outStartS - 1)).toBe(0);
    expect(near(progress(p, p.outStartS + p.durationS / 2), 0.5)).toBe(true);
    expect(progress(p, p.outStartS + 999)).toBe(1);
  });
  it('tempo ramps back to 1 after the mix', () => {
    const q = { ...p, rate: 0.95, inBeatS: 0.5 };
    expect(rampRate(q, 0)).toBe(0.95);
    expect(near(rampRate(q, RAMP_BEATS * 0.5), 1)).toBe(true);
    expect(rampRate(q, 1e6)).toBe(1);
    const mid = rampRate(q, (RAMP_BEATS * 0.5) / 2);
    expect(mid).toBeGreaterThan(0.95);
    expect(mid).toBeLessThan(1);
  });
  it('phase lock: nudges within ±2 %, seeks on big errors', () => {
    expect(correctedRate(1, 0.001)).toBe(1);
    expect(correctedRate(1, 0.01)!).toBeLessThan(1);   // ahead → slower
    expect(correctedRate(1, -0.01)!).toBeGreaterThan(1);
    expect(correctedRate(1, 0.1)).toBe(0.98);
    expect(correctedRate(1, 0.2)).toBeNull();
  });
});

describe('settings', () => {
  it('DJ only between non-album-consecutive tracks; crossfade fallback length', () => {
    const s = { ...defaultListening(), transition: 'dj' as const };
    const x = tr('x', null, 240, 'alb');
    const y = { ...tr('y', null, 240, 'alb'), trackNo: 2 };
    expect(djFor(s, x, y)).toBe(false);
    expect(djFor(s, x, tr('z', null))).toBe(true);
    expect(crossfadeFor(s, x, tr('z', null))).toBe(6);
    expect(crossfadeFor({ ...s, transition: 'off', crossfadeS: 5 }, x, tr('z', null))).toBe(0);
    expect(crossfadeFor({ ...s, transition: 'crossfade', crossfadeS: 5 }, x, tr('z', null))).toBe(5);
  });
});
