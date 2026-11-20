import { describe, expect, it } from 'vitest';
import type { Track } from './types';
import {
  insertTracks,
  moveTrack,
  nextIndex,
  pickSlot,
  prevIndex,
  queueLabel,
  removeTrack,
  shuffleOrder,
  sortQueue,
  totalDurationS,
  type QueueModel,
} from './queue';

const t = (id: string, d = 100): Track => ({
  id,
  title: id.toUpperCase(),
  artist: 'ar' + id,
  artistId: 'a',
  album: 'al',
  albumId: 'b',
  durationS: d,
  starred: false,
});
const q = (index = 0, ids = ['a', 'b', 'c', 'd']): QueueModel => ({ id: 'q', name: 'Q', slot: 1, tracks: ids.map((i) => t(i)), index });
const ids = (m: QueueModel) => m.tracks.map((x) => x.id).join('');

describe('moveTrack', () => {
  it('moves the current track and follows it', () => {
    const m = moveTrack(q(1), 1, 3);
    expect(ids(m)).toBe('acdb');
    expect(m.index).toBe(3);
  });
  it('keeps the current track when items move across it', () => {
    expect(moveTrack(q(2), 0, 3).index).toBe(1); // a moved after c
    expect(moveTrack(q(1), 3, 0).index).toBe(2); // d moved before b
    expect(moveTrack(q(1), 2, 3).index).toBe(1); // unaffected
  });
  it('ignores out-of-range moves', () => {
    const base = q(0);
    expect(moveTrack(base, 0, 9)).toBe(base);
  });
});

describe('insertTracks', () => {
  it('plays next after the current track', () => {
    const m = insertTracks(q(1), [t('x')], 'next');
    expect(ids(m)).toBe('abxcd');
    expect(m.index).toBe(1);
  });
  it('appends at the end', () => expect(ids(insertTracks(q(0), [t('x'), t('y')], 'end'))).toBe('abcdxy'));
  it('shifts the current index when inserting before it', () => {
    const m = insertTracks(q(2), [t('x')], 0);
    expect(ids(m)).toBe('xabcd');
    expect(m.tracks[m.index].id).toBe('c');
  });
  it('works on an empty queue', () => {
    const m = insertTracks({ ...q(0, []) }, [t('x')], 'next');
    expect(ids(m)).toBe('x');
    expect(m.index).toBe(0);
  });
});

describe('removeTrack', () => {
  it('keeps the current track when removing before it', () => {
    const m = removeTrack(q(2), 0);
    expect(ids(m)).toBe('bcd');
    expect(m.tracks[m.index].id).toBe('c');
  });
  it('removing the current track makes the next one current', () => {
    const m = removeTrack(q(1), 1);
    expect(m.tracks[m.index].id).toBe('c');
  });
  it('clamps at the end', () => expect(removeTrack(q(3), 3).index).toBe(2));
});

describe('next/prev', () => {
  it('stops at the end with repeat off', () => {
    expect(nextIndex(q(3), 'off', false)).toBeNull();
    expect(nextIndex(q(3), 'all', false)).toBe(0);
    expect(nextIndex(q(2), 'one', false)).toBe(2);
    expect(nextIndex(q(2), 'one', true)).toBe(3);
  });
  it('follows shuffle order', () => {
    const m = { ...q(2), order: [2, 0, 3, 1] };
    expect(nextIndex(m, 'off', true)).toBe(0);
    expect(nextIndex({ ...m, index: 1 }, 'off', true)).toBeNull();
    expect(nextIndex({ ...m, index: 1 }, 'all', true)).toBe(2);
    expect(prevIndex({ ...m, index: 3 }, 'off')).toBe(0);
  });
  it('skips tracks hidden by family mode', () => {
    const m = q(0);
    m.tracks[1] = { ...m.tracks[1], familyHidden: true };
    m.tracks[2] = { ...m.tracks[2], familyHidden: true };
    expect(nextIndex(m, 'off', false)).toBe(3);
    expect(prevIndex({ ...m, index: 3 }, 'off')).toBe(0);
    expect(nextIndex({ ...m, index: 3 }, 'all', false)).toBe(0);
    expect(nextIndex({ ...m, index: 1 }, 'one', false)).toBe(3); // a hidden current track is not repeated
    const all = { ...m, tracks: m.tracks.map((x) => ({ ...x, familyHidden: true })) };
    expect(nextIndex(all, 'all', true)).toBeNull();
  });
  it('prev goes back or wraps', () => {
    expect(prevIndex(q(2), 'off')).toBe(1);
    expect(prevIndex(q(0), 'off')).toBe(0);
    expect(prevIndex(q(0), 'all')).toBe(3);
  });
});

it('shuffleOrder is a permutation starting with the current track', () => {
  let s = 1;
  const rng = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const o = shuffleOrder(10, 4, rng);
  expect(o[0]).toBe(4);
  expect([...o].sort((a, b) => a - b)).toEqual([...Array(10).keys()]);
});

it('sorts and keeps the current track', () => {
  const m = sortQueue({ ...q(0, ['c', 'a', 'b']) }, 'title');
  expect(ids(m)).toBe('abc');
  expect(m.tracks[m.index].id).toBe('c');
  expect(ids(sortQueue(q(0), 'reverse'))).toBe('dcba');
});

it('labels and totals', () => {
  expect(queueLabel({ slot: 20, name: 'Search: conver' })).toBe('20. Search: conver');
  expect(totalDurationS([t('a', 144), t('b', 230)])).toBe(374);
});

describe('pickSlot', () => {
  it('uses the first free slot', () => {
    expect(pickSlot([{ id: 'a', slot: 1 }, { id: 'b', slot: 3 }], null)).toEqual({ slot: 2, replaces: null });
  });
  it('replaces the oldest non-active queue when all 20 are used', () => {
    const all = Array.from({ length: 20 }, (_, i) => ({ id: 'q' + i, slot: i + 1, updatedAt: `2026-01-${String(30 - i).padStart(2, '0')}` }));
    expect(pickSlot(all, 'q19')).toEqual({ slot: 19, replaces: 'q18' });
  });
});
