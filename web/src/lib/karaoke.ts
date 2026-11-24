/**
 * Karaoke: turn the singer down by cancelling the centre of the stereo image (no AI).
 *
 * Lead vocals are usually mixed dead centre. With M = (L+R)/2 and S = (L-R)/2 the output is
 *   L' = S + k·M + (1-k)·LP(M),  R' = -S + k·M + (1-k)·LP(M)
 * so the centre drops to k (-20 dB) above ~150 Hz while bass and kick (also centred) survive through a 4th-order
 * low-pass. Stereo-panned instruments and reverb stay. Same maths as the server's `/stream?karaoke=true` ffmpeg
 * filter (server/nasmusic/stream/transcode.py) and Android's KaraokeProcessor, so it sounds the same everywhere.
 */
import type { AudioCtxLike } from './eq';

export const KARAOKE_KEEP = 0.1;
export const KARAOKE_BASS_HZ = 150;

/** Per-sample reference of the filter's mixing matrix (the low-pass part given as `lowMid`). For tests. */
export function karaokeSample(l: number, r: number, lowMid: number, keep = KARAOKE_KEEP): [number, number] {
  const m = (l + r) / 2;
  const s = (l - r) / 2;
  const centre = keep * m + (1 - keep) * lowMid;
  return [centre + s, centre - s];
}

export interface KaraokeNode {
  input: AudioNode;
  output: AudioNode;
  set(on: boolean): void;
}

function setParam(p: AudioParam, v: number, t: number) {
  if (typeof p.setTargetAtTime === 'function') p.setTargetAtTime(v, t, 0.03);
  else p.value = v;
}

function gain(ctx: AudioCtxLike, v: number): GainNode {
  const g = ctx.createGain();
  g.gain.value = v;
  return g;
}

/** input → (dry | karaoke) → output; `set` crossfades between them so the switch doesn't click. */
export function buildKaraoke(ctx: AudioCtxLike, keep = KARAOKE_KEEP, bassHz = KARAOKE_BASS_HZ): KaraokeNode {
  const input = gain(ctx, 1);
  const output = gain(ctx, 1);
  const dry = gain(ctx, 1);
  const wet = gain(ctx, 0);
  input.connect(dry);
  dry.connect(output);

  const split = ctx.createChannelSplitter(2);
  const merge = ctx.createChannelMerger(2);
  input.connect(split);

  // mid = (L + R) / 2
  const mid = gain(ctx, 1);
  const lHalf = gain(ctx, 0.5);
  const rHalf = gain(ctx, 0.5);
  split.connect(lHalf, 0);
  split.connect(rHalf, 1);
  lHalf.connect(mid);
  rHalf.connect(mid);

  // a little of the full centre, plus its low end
  const keepMid = gain(ctx, keep);
  mid.connect(keepMid);
  const lp1 = ctx.createBiquadFilter();
  const lp2 = ctx.createBiquadFilter();
  for (const lp of [lp1, lp2]) {
    lp.type = 'lowpass';
    lp.frequency.value = bassHz;
    lp.Q.value = Math.SQRT1_2;
  }
  const lowMid = gain(ctx, 1 - keep);
  mid.connect(lp1);
  lp1.connect(lp2);
  lp2.connect(lowMid);
  for (const ch of [0, 1]) {
    keepMid.connect(merge, 0, ch);
    lowMid.connect(merge, 0, ch);
  }

  // side: L' gets (L - R)/2, R' gets (R - L)/2
  const lToL = gain(ctx, 0.5);
  const rToL = gain(ctx, -0.5);
  const rToR = gain(ctx, 0.5);
  const lToR = gain(ctx, -0.5);
  split.connect(lToL, 0);
  split.connect(lToR, 0);
  split.connect(rToL, 1);
  split.connect(rToR, 1);
  lToL.connect(merge, 0, 0);
  rToL.connect(merge, 0, 0);
  rToR.connect(merge, 0, 1);
  lToR.connect(merge, 0, 1);

  merge.connect(wet);
  wet.connect(output);

  return {
    input,
    output,
    set(on: boolean) {
      const t = ctx.currentTime;
      setParam(dry.gain, on ? 0 : 1, t);
      setParam(wet.gain, on ? 1 : 0, t);
    },
  };
}
