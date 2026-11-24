import { describe, expect, it } from 'vitest';
import { addRange, splitDuplicates, toggleTrack } from './tracksel';
import type { Track } from './types';

const t = (id: string) => ({ id, title: id }) as Track;
const ids = (x: Track[]) => x.map((y) => y.id);

describe('track selection', () => {
  it('toggles songs in pick order', () => {
    let s = toggleTrack([], t('b'));
    s = toggleTrack(s, t('a'));
    expect(ids(s)).toEqual(['b', 'a']);
    expect(ids(toggleTrack(s, t('b')))).toEqual(['a']);
  });
  it('adds a range either direction without duplicates', () => {
    const list = ['a', 'b', 'c', 'd'].map(t);
    expect(ids(addRange([t('c')], list, 3, 1))).toEqual(['c', 'b', 'd']);
    expect(ids(addRange([], list, 0, 1))).toEqual(['a', 'b']);
  });
  it('splits duplicates', () => {
    const r = splitDuplicates(['a', 'b', 'c'].map(t), new Set(['b']));
    expect(ids(r.dup)).toEqual(['b']);
    expect(ids(r.fresh)).toEqual(['a', 'c']);
  });
});
