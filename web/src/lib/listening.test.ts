import { describe, expect, it } from 'vitest';
import {
  crossfadeFor,
  defaultListening,
  fadeIn,
  fadeOut,
  followsOnAlbum,
  levellingGain,
  loadListening,
  normaliseListening,
  playingAlbumInOrder,
  saveListening,
  sleepFadeLevel,
  sleepLabel,
  sleepStopsAt,
  useAlbumGain,
} from './listening';
import type { QueueModel } from './queue';
import type { Track } from './types';

const t = (id: string, albumId: string, trackNo: number | null, extra: Partial<Track> = {}): Track => ({
  id, title: id, artistId: 'ar', artist: 'A', albumId, album: albumId, trackNo, discNo: 1, durationS: 200, starred: false, ...extra,
});

describe('albums in order', () => {
  it('detects consecutive album tracks, including across discs', () => {
    expect(followsOnAlbum(t('1', 'x', 1), t('2', 'x', 2))).toBe(true);
    expect(followsOnAlbum(t('1', 'x', 1), t('3', 'x', 3))).toBe(false);
    expect(followsOnAlbum(t('1', 'x', 1), t('2', 'y', 2))).toBe(false);
    expect(followsOnAlbum(t('9', 'x', 9, { discNo: 1 }), t('10', 'x', 1, { discNo: 2 }))).toBe(true);
    expect(followsOnAlbum(t('1', 'x', null), t('2', 'x', null))).toBe(true);
    expect(followsOnAlbum(null, t('2', 'x', 2))).toBe(false);
  });
  it('album mode only for an unshuffled queue playing an album in order', () => {
    const q: QueueModel = { id: 'q', name: 'q', slot: 1, index: 1, tracks: [t('a', 'x', 1), t('b', 'x', 2), t('c', 'y', 5)] };
    expect(playingAlbumInOrder(q, 1)).toBe(true);
    expect(playingAlbumInOrder(q, 2)).toBe(false);
    expect(playingAlbumInOrder({ ...q, order: [0, 1, 2] }, 1)).toBe(false);
    const s = { ...defaultListening(), levelling: 'auto' as const };
    expect(useAlbumGain(s, q, 1)).toBe(true);
    expect(useAlbumGain(s, q, 2)).toBe(false);
    expect(useAlbumGain({ ...s, levelling: 'album' }, q, 2)).toBe(true);
    expect(useAlbumGain({ ...s, levelling: 'track' }, q, 1)).toBe(false);
  });
});

describe('crossfade', () => {
  const s = { ...defaultListening(), crossfadeS: 8 };
  it('is off by default and skipped between consecutive album tracks', () => {
    expect(defaultListening().crossfadeS).toBe(0);
    expect(crossfadeFor(defaultListening(), t('1', 'x', 1), t('2', 'y', 1))).toBe(0);
    expect(crossfadeFor(s, t('1', 'x', 1), t('2', 'x', 2))).toBe(0);
    expect(crossfadeFor(s, t('1', 'x', 1), t('2', 'y', 1))).toBe(8);
    expect(crossfadeFor(s, t('1', 'x', 1), t('1', 'x', 1))).toBe(0);
  });
  it('never takes more than a third of a short track', () => {
    expect(crossfadeFor(s, t('1', 'x', 1, { durationS: 12 }), t('2', 'y', 1))).toBe(4);
  });
  it('equal-power curves', () => {
    for (const x of [0, 0.25, 0.5, 0.9, 1]) expect(fadeIn(x) ** 2 + fadeOut(x) ** 2).toBeCloseTo(1, 6);
    expect(fadeIn(0)).toBe(0);
    expect(fadeOut(1)).toBeCloseTo(0, 9);
  });
});

describe('levelling gain', () => {
  const rg = { trackGain: -6, trackPeak: 0.5, albumGain: -4, albumPeak: 0.9 };
  const s = { ...defaultListening(), levelling: 'track' as const, preventClipping: false };
  it('applies track or album gain plus preamp', () => {
    expect(levellingGain(t('1', 'x', 1, { replayGain: rg }), s, false)).toBeCloseTo(10 ** (-6 / 20));
    expect(levellingGain(t('1', 'x', 1, { replayGain: rg }), s, true)).toBeCloseTo(10 ** (-4 / 20));
    expect(levellingGain(t('1', 'x', 1, { replayGain: rg }), { ...s, preampDb: 3 }, false)).toBeCloseTo(10 ** (-3 / 20));
    expect(levellingGain(t('1', 'x', 1, { replayGain: { trackGain: -2 } }), s, true)).toBeCloseTo(10 ** (-2 / 20));
  });
  it('off, unlevelled tracks, clipping prevention', () => {
    expect(levellingGain(t('1', 'x', 1, { replayGain: rg }), { ...s, levelling: 'off' }, false)).toBe(1);
    expect(levellingGain(t('1', 'x', 1), { ...s, preampDb: -6 }, false)).toBeCloseTo(10 ** (-6 / 20));
    const quiet = t('1', 'x', 1, { replayGain: { trackGain: 10, trackPeak: 0.5 } });
    expect(levellingGain(quiet, s, false)).toBeCloseTo(10 ** 0.5);
    expect(levellingGain(quiet, { ...s, preventClipping: true }, false)).toBeCloseTo(2); // 1 / peak
  });
});

describe('settings', () => {
  it('round-trip and clamp', () => {
    const store = new Map<string, string>();
    const st = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    expect(loadListening(st)).toEqual(defaultListening());
    saveListening(st, { crossfadeS: 5, levelling: 'album', preampDb: -2, preventClipping: false });
    expect(loadListening(st)).toEqual({ crossfadeS: 5, levelling: 'album', preampDb: -2, preventClipping: false });
    expect(normaliseListening({ crossfadeS: 99, levelling: 'loud' as never, preampDb: -40 })).toEqual({ ...defaultListening(), crossfadeS: 12, preampDb: -15 });
  });
});

describe('sleep timer', () => {
  it('stops at the end of the track or album', () => {
    const a1 = t('1', 'x', 1), a2 = t('2', 'x', 2), b1 = t('3', 'y', 1);
    expect(sleepStopsAt(null, a1, a2)).toBe(false);
    expect(sleepStopsAt({ mode: 'track' }, a1, a2)).toBe(true);
    expect(sleepStopsAt({ mode: 'album', albumId: 'x' }, a1, a2)).toBe(false);
    expect(sleepStopsAt({ mode: 'album', albumId: 'x' }, a2, b1)).toBe(true);
    expect(sleepStopsAt({ mode: 'album', albumId: 'x' }, a2, null)).toBe(true);
    expect(sleepStopsAt({ mode: 'minutes', endsAt: 1 }, a1, b1)).toBe(false);
  });
  it('fades out over the last seconds', () => {
    expect(sleepFadeLevel(30)).toBe(1);
    expect(sleepFadeLevel(5)).toBeCloseTo(0.25);
    expect(sleepFadeLevel(0)).toBe(0);
    expect(sleepLabel({ mode: 'minutes', endsAt: 1000 + 14.5 * 60000 }, 1000)).toBe('15 min');
    expect(sleepLabel(null)).toBe('Off');
  });
});
