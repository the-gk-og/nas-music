// DJ automix transitions: pure planning maths (tested in automix.test.ts). The audio side is in player.svelte.ts.
// Same rules as android .../playback/Automix.kt. Server analysis: Track.mix (api 0.7, schema v7 track_dj).
//
// A plan says: when the outgoing track reaches `outStartS` (its media time), start the incoming track at
// `inStartS`, playing at `rate` × the user's speed, so that its downbeats land on the outgoing track's
// downbeats. Over `durationS` (outgoing media seconds; = `beats` beats) the volumes and bass follow the curves
// below. Afterwards the incoming rate eases back to 1 over RAMP_BEATS. When the tracks can't be beat-matched
// (no stable grid, tempos too far apart, tempo change not allowed) but both have mix points, the plan is a
// plain equal-power crossfade between the mix points (`beatmatch: false`). With no analysis: null → the
// player's normal crossfade.
import type { Track, TrackMix } from './types';

export type TransitionMode = 'off' | 'crossfade' | 'dj';

/** Beyond ±8 % a time-stretch is audible on most material; the server's ordering uses the same limit. */
export const MAX_STRETCH = 0.08;
/** "Allow tempo change" off: only beat-match tracks that already agree within this. */
export const NO_STRETCH_TOLERANCE = 0.003;
export const DJ_BEATS = [8, 16, 32] as const;
/** After the transition the incoming track eases back to its own tempo over this many of its beats. */
export const RAMP_BEATS = 32;
/** Bass cut on the side that isn't carrying the low end. */
export const BASS_KILL_DB = -26;
/** A phase error above this is fixed with a seek, below it by nudging the rate. */
export const SEEK_ERROR_S = 0.12;
/** Shortest beat-matched transition worth doing. */
const MIN_BEATS = 8;

export interface DjOptions {
  beats: number;
  allowTempo: boolean;
  /** Length of the plain crossfade between mix points when beat-matching isn't possible. */
  fallbackS: number;
}

export interface DjPlan {
  beatmatch: boolean;
  /** Outgoing media time where the transition starts. */
  outStartS: number;
  /** Incoming media time at that moment. */
  inStartS: number;
  /** Transition length in outgoing media seconds. */
  durationS: number;
  beats: number;
  /** Incoming playback rate relative to the outgoing one (1 when not beat-matching). */
  rate: number;
  /** Incoming tempo multiplier m (1, 2 or ½): incoming.bpm × m × rate = outgoing.bpm. */
  multiplier: number;
  /** Seconds per incoming beat at its own tempo (for the ramp back). */
  inBeatS: number;
}

const usable = (m: TrackMix | null | undefined): m is TrackMix & { mixInS: number; mixOutS: number } =>
  !!m && typeof m.mixInS === 'number' && typeof m.mixOutS === 'number';

const gridOk = (m: TrackMix | null | undefined) =>
  !!m && m.gridStable && !!m.bpm && m.bpm > 0 && typeof m.downbeatS === 'number';

/** (multiplier, rate) making b's beats match a's, choosing the smallest stretch among m ∈ {1, 2, ½}. */
export function tempoMatch(aBpm: number, bBpm: number): { multiplier: number; rate: number } {
  let best = { multiplier: 1, rate: aBpm / bBpm };
  for (const m of [2, 0.5]) {
    const r = aBpm / (bBpm * m);
    if (Math.abs(Math.log(r)) < Math.abs(Math.log(best.rate)) - 1e-9) best = { multiplier: m, rate: r };
  }
  return best;
}

/** Snap t down/up/nearest to the bar grid starting at `downbeat`. */
export function snapBar(t: number, downbeat: number, bar: number, mode: 'floor' | 'ceil' | 'nearest' = 'nearest'): number {
  const x = (t - downbeat) / bar;
  const n = mode === 'floor' ? Math.floor(x + 1e-6) : mode === 'ceil' ? Math.ceil(x - 1e-6) : Math.round(x);
  return downbeat + Math.max(0, n) * bar;
}

/** Normalise the beats setting to 8, 16 or 32. */
export function djBeats(v: unknown): number {
  const n = typeof v === 'number' && isFinite(v) ? v : 16;
  return DJ_BEATS.reduce((best, b) => (Math.abs(b - n) < Math.abs(best - n) ? b : best), 16 as number);
}

export function planTransition(a: Track | null | undefined, b: Track | null | undefined, o: DjOptions): DjPlan | null {
  if (!a || !b || a.id === b.id) return null;
  const am = a.mix, bm = b.mix;
  if (!usable(am) || !usable(bm)) return null;
  const aEnd = Math.min(am.lastBeatS ?? a.durationS, a.durationS || Infinity);
  if (gridOk(am) && gridOk(bm)) {
    const { multiplier, rate } = tempoMatch(am.bpm!, bm.bpm!);
    const stretch = Math.abs(rate - 1);
    const ok = o.allowTempo ? stretch <= MAX_STRETCH : stretch <= NO_STRETCH_TOLERANCE;
    if (ok) {
      const beat = 60 / am.bpm!;
      const bpb = am.beatsPerBar || 4;
      const bar = beat * bpb;
      let beats = djBeats(o.beats);
      let outStart = snapBar(am.mixOutS, am.downbeatS!, bar, 'nearest');
      // finish by the last beat: move the start earlier (to a bar line) if the outro is too short
      if (outStart + beats * beat > aEnd + 1e-6) outStart = snapBar(aEnd - beats * beat, am.downbeatS!, bar, 'floor');
      // ... but never into the first half of the song: shorten the transition instead
      const earliest = Math.max(am.mixInS, (a.durationS || aEnd) * 0.5);
      if (outStart < earliest) {
        outStart = snapBar(Math.max(am.mixOutS, earliest), am.downbeatS!, bar, 'ceil');
        beats = Math.floor((aEnd - outStart) / beat / bpb) * bpb;
      }
      const inBeat = 60 / bm.bpm!;
      const inStart = snapBar(bm.mixInS, bm.downbeatS!, inBeat * (bm.beatsPerBar || 4), 'nearest');
      // the incoming track must have room for the whole transition
      const inRoom = ((b.durationS || Infinity) - inStart) / rate;
      if (beats * beat > inRoom) beats = Math.floor(inRoom / beat / bpb) * bpb;
      if (beats >= MIN_BEATS && outStart >= 0) {
        return { beatmatch: true, outStartS: outStart, inStartS: inStart, durationS: beats * beat, beats, rate, multiplier, inBeatS: inBeat };
      }
    }
  }
  // Mix points without beat-matching: equal-power crossfade from the outro to after the intro's silence.
  const len = Math.max(1, Math.min(o.fallbackS, (a.durationS || aEnd) / 3, (b.durationS || Infinity) / 3));
  let outStart = am.mixOutS;
  if (outStart + len > aEnd) outStart = aEnd - len;
  outStart = Math.max(outStart, (a.durationS || aEnd) * 0.5);
  if (outStart + len > (a.durationS || aEnd) + 1e-6) return null;
  return { beatmatch: false, outStartS: outStart, inStartS: Math.max(0, bm.mixInS), durationS: len, beats: 0, rate: 1, multiplier: 1, inBeatS: 0 };
}

/** Where the incoming track should be (its media time) when the outgoing one is at `outPos`. */
export function expectedIn(p: DjPlan, outPos: number): number {
  return p.inStartS + (outPos - p.outStartS) * p.rate;
}

/** Progress 0..1 through the transition at outgoing media time `outPos`. */
export function progress(p: DjPlan, outPos: number): number {
  return Math.max(0, Math.min(1, (outPos - p.outStartS) / p.durationS));
}

const s = (x: number) => Math.sin(Math.max(0, Math.min(1, x)) * Math.PI / 2);
const c = (x: number) => Math.cos(Math.max(0, Math.min(1, x)) * Math.PI / 2);

/** Volume of the incoming track (0..1). DJ style: up to full by the middle; else an equal-power crossfade. */
export function volIn(p: DjPlan, x: number): number {
  return p.beatmatch ? s(x * 2) : s(x);
}

/** Volume of the outgoing track: full until the middle, then out (DJ); equal-power otherwise. */
export function volOut(p: DjPlan, x: number): number {
  return p.beatmatch ? c((x - 0.5) * 2) : c(x);
}

/** Low-shelf gain in dB for the incoming track: cut until the bass swap at the middle (over one beat). */
export function bassIn(p: DjPlan, x: number): number {
  if (!p.beatmatch) return 0;
  const half = 0.5 / Math.max(p.beats, 1); // one beat wide, centred on the middle
  const y = (x - (0.5 - half)) / (2 * half);
  return BASS_KILL_DB * (1 - Math.max(0, Math.min(1, y))) + 0; // + 0: no -0
}

/** Low-shelf gain in dB for the outgoing track: the mirror image. */
export function bassOut(p: DjPlan, x: number): number {
  return p.beatmatch ? BASS_KILL_DB - bassIn(p, x) : 0;
}

/** Incoming rate `secondsAfter` (its media seconds) after the transition ended: eases from p.rate to 1. */
export function rampRate(p: DjPlan, secondsAfter: number): number {
  if (!p.beatmatch || p.rate === 1) return 1;
  const len = RAMP_BEATS * p.inBeatS;
  const x = Math.max(0, Math.min(1, secondsAfter / len));
  return p.rate + (1 - p.rate) * (0.5 - 0.5 * Math.cos(Math.PI * x));
}

/** Phase-lock: the rate to play the incoming track at, given its phase error (actual − expected, seconds).
 *  Returns null when the error is too big to nudge (seek instead). Corrections are capped at ±2 %. */
export function correctedRate(rate: number, errorS: number): number | null {
  if (Math.abs(errorS) > SEEK_ERROR_S) return null;
  if (Math.abs(errorS) < 0.004) return rate;
  const k = Math.max(-0.02, Math.min(0.02, errorS * 0.4)); // 10 ms ahead → 0.4 % slower
  return rate * (1 - k);
}
