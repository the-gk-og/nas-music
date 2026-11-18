import { describe, expect, it } from 'vitest';
import type { Shelf } from './types';
import {
  appendNew,
  arrangeShelves,
  classifySwipe,
  effectiveMotion,
  formFor,
  greeting,
  matchKey,
  motionMs,
  moveInList,
  needsTopUp,
  parseRadioId,
  tabletSidePane,
} from './v3';

describe('screen forms', () => {
  it('uses the §6 breakpoints', () => {
    expect(formFor(375)).toBe('phone');
    expect(formFor(599)).toBe('phone');
    expect(formFor(600)).toBe('tablet');
    expect(formFor(1199)).toBe('tablet');
    expect(formFor(1200)).toBe('desktop');
  });
  it('shows a side pane on tablet landscape only', () => {
    expect(tabletSidePane(1000, 700)).toBe(true);
    expect(tabletSidePane(800, 1100)).toBe(false);
    expect(tabletSidePane(1400, 900)).toBe(false);
  });
});

describe('motion', () => {
  it('respects reduce-motion', () => {
    expect(effectiveMotion('expressive', true)).toBe('off');
    expect(effectiveMotion('quick', false)).toBe('quick');
  });
  it('keeps Quick at or under 200 ms', () => {
    expect(motionMs('quick', 'cover')).toBeLessThanOrEqual(200);
    expect(motionMs('off')).toBe(0);
    expect(motionMs('expressive', 'cover')).toBeGreaterThan(200);
  });
});

describe('arrangeShelves', () => {
  const sh = (id: string, n = 1): Shelf => ({ id, title: id, items: Array.from({ length: n }, (_, i) => ({ type: 'album', id: id + i, title: 'x' })) });
  const all = [sh('radio', 0), sh('devices', 0), sh('recent'), sh('continue', 0), sh('pinned')];
  it('follows the user order, hides hidden and empty shelves', () => {
    const out = arrangeShelves(all, { order: ['pinned', 'recent', 'radio'], hidden: ['recent'] });
    expect(out.map((s) => s.id)).toEqual(['pinned', 'radio']);
  });
  it('shows the devices shelf only when other devices are online', () => {
    expect(arrangeShelves(all, { order: [], hidden: [] }, new Set(['devices'])).map((s) => s.id)).toContain('devices');
    expect(arrangeShelves(all, { order: [], hidden: [] }).map((s) => s.id)).not.toContain('devices');
  });
  it('orders "because:" shelves by their kind', () => {
    const out = arrangeShelves([sh('mixes'), sh('because:t1'), sh('because:t2')], { order: ['because', 'mixes'], hidden: [] });
    expect(out.map((s) => s.id)).toEqual(['because:t1', 'because:t2', 'mixes']);
  });
  it('moves items in a list', () => {
    expect(moveInList(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']);
  });
});

describe('radio', () => {
  it('tops up when five or fewer remain', () => {
    expect(needsTopUp(25, 18)).toBe(false);
    expect(needsTopUp(25, 19)).toBe(true);
    expect(needsTopUp(3, 0)).toBe(true);
  });
  it('parses shelf radio ids', () => {
    expect(parseRadioId('artist:ar2')).toEqual({ type: 'artist', id: 'ar2' });
    expect(parseRadioId('track:t0_1')).toEqual({ type: 'track', id: 't0_1' });
    expect(parseRadioId('random')).toEqual({ type: 'random' });
    expect(parseRadioId('weird:x')).toEqual({ type: 'random' });
  });
  it('appends only the new tracks', () => {
    const a = [{ id: '1' }, { id: '2' }];
    expect(appendNew(a, [{ id: '1' }, { id: '2' }, { id: '3' }])).toEqual([{ id: '1' }, { id: '2' }, { id: '3' }]);
    expect(appendNew(a, [{ id: '1' }])).toBe(a);
    // local was reordered: keep local order, add unseen ids
    expect(appendNew([{ id: '2' }, { id: '1' }], [{ id: '1' }, { id: '2' }, { id: '4' }]).map((x) => x.id)).toEqual(['2', '1', '4']);
  });
});

describe('gestures', () => {
  it('classifies swipes and taps', () => {
    expect(classifySwipe(-120, 10, 200)).toBe('left');
    expect(classifySwipe(90, -5, 200)).toBe('right');
    expect(classifySwipe(4, 140, 250)).toBe('down');
    expect(classifySwipe(0, -80, 250)).toBe('up');
    expect(classifySwipe(2, 3, 120)).toBe('tap');
    expect(classifySwipe(60, 55, 200)).toBeNull(); // diagonal
    expect(classifySwipe(30, 0, 200)).toBeNull(); // too short
  });
});

it('greets by time of day', () => {
  expect(greeting(8)).toBe('Good morning');
  expect(greeting(15)).toBe('Good afternoon');
  expect(greeting(21)).toBe('Good evening');
});

it('matches owned music by normalised artist + title', () => {
  expect(matchKey('Beyoncé & Jay', 'Halo (Remastered)')).toBe(matchKey('beyonce and jay', 'Halo'));
});
