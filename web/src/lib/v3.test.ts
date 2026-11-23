import { describe, expect, it } from 'vitest';
import type { Shelf } from './types';
import {
  accentColor,
  appendNew,
  arrangeShelves,
  classifySwipe,
  desktopPortrait,
  effectiveMotion,
  formFor,
  greeting,
  matchKey,
  motionMs,
  moveInList,
  needsTopUp,
  parseRadioId,
  progressFraction,
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
  it('keeps the desktop layout in a tall window with a mouse (portrait monitor)', () => {
    expect(formFor(1080, 1920, true)).toBe('desktop');
    expect(formFor(960, 1040, true)).toBe('desktop'); // half of a 1920x1080 screen
    expect(formFor(1080, 960, true)).toBe('tablet'); // half-height: wider than tall
    expect(formFor(1080, 1920, false)).toBe('tablet'); // touch tablet in portrait stays a tablet
    expect(formFor(800, 1280, true)).toBe('tablet');
    expect(formFor(375, 812, true)).toBe('phone');
    expect(desktopPortrait(1440, 2560, true)).toBe(true);
    expect(desktopPortrait(1200, 1900, false)).toBe(true);
    expect(desktopPortrait(1920, 1080, true)).toBe(false);
    expect(desktopPortrait(1080, 1920, false)).toBe(false);
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
  const sh = (id: string, n = 1, kind = id): Shelf => ({ id, kind, title: id, items: Array.from({ length: n }, (_, i) => ({ type: 'album', id: id + i, title: 'x' })) });
  const all = [sh('start_radio', 0), sh('devices', 0), sh('recently_played'), sh('continue_listening', 0), sh('pinned')];
  it('follows the user order, hides hidden and empty shelves', () => {
    const out = arrangeShelves(all, { order: ['pinned', 'recently_played', 'start_radio'], hidden: ['recently_played'] });
    expect(out.map((s) => s.id)).toEqual(['pinned', 'start_radio']);
  });
  it('shows the devices shelf only when other devices are online', () => {
    expect(arrangeShelves(all, { order: [], hidden: [] }, new Set(['devices'])).map((s) => s.id)).toContain('devices');
    expect(arrangeShelves(all, { order: [], hidden: [] }).map((s) => s.id)).not.toContain('devices');
  });
  it('orders "byp:" shelves by their kind', () => {
    const byp = 'because_you_played';
    const out = arrangeShelves([sh('mixes'), sh('byp:album:1', 1, byp), sh('byp:track:2', 1, byp)], { order: [byp, 'mixes'], hidden: [] });
    expect(out.map((s) => s.id)).toEqual(['byp:album:1', 'byp:track:2', 'mixes']);
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
    expect(parseRadioId('anything', { type: 'album', id: 'a1' })).toEqual({ type: 'album', id: 'a1' });
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

it('resolves accents and progress', () => {
  expect(accentColor('Teal')).toBe('#2dd4bf');
  expect(accentColor('#123abc')).toBe('#123abc');
  expect(accentColor('nope')).toBe('#4ade80');
  expect(progressFraction({ index: 3, count: 12 })).toBe(0.25);
  expect(progressFraction(null)).toBeNull();
});

it('greets by time of day', () => {
  expect(greeting(8)).toBe('Good morning');
  expect(greeting(15)).toBe('Good afternoon');
  expect(greeting(21)).toBe('Good evening');
});

it('matches owned music by normalised artist + title', () => {
  expect(matchKey('Beyoncé & Jay', 'Halo (Remastered)')).toBe(matchKey('beyonce and jay', 'Halo'));
});

describe('AI radio helpers', () => {
  it('builds the smart-shuffle context with ISO weekdays', async () => {
    const { radioContext } = await import('./v3');
    expect(radioContext(new Date(2026, 10, 20, 19, 5), 'web')).toEqual({ localHour: 19, weekday: 5, deviceKind: 'web' });
    expect(radioContext(new Date(2026, 10, 22, 8), 'desktop')).toEqual({ localHour: 8, weekday: 7, deviceKind: 'desktop' });
  });
  it('replaces only what comes after the current and next track', async () => {
    const { replaceUpcoming } = await import('./v3');
    const t = (id: string) => ({ id });
    const local = ['a', 'b', 'c', 'd', 'e'].map(t);
    const server = ['a', 'b', 'c', 'x', 'y', 'z'].map(t);
    expect(replaceUpcoming(local, server, 1).map((x) => x.id)).toEqual(['a', 'b', 'c', 'x', 'y', 'z']);
    expect(replaceUpcoming(local, ['a', 'b', 'c', 'b', 'q'].map(t), 1).map((x) => x.id)).toEqual(['a', 'b', 'c', 'q']);
  });
  it('parses moment radio ids and drops chips', async () => {
    const { parseRadioId, withoutChip } = await import('./v3');
    expect(parseRadioId('moment')).toEqual({ type: 'moment' });
    const chips = [{ id: 'a', kind: 'energy' as const, label: 'Low energy' }, { id: 'b', kind: 'mood' as const, label: 'Chill' }];
    expect(withoutChip(chips, 'a').map((c) => c.id)).toEqual(['b']);
  });
});
