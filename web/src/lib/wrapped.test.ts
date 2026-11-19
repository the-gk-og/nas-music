import { describe, expect, it } from 'vitest';
import type { Wrapped } from './types';
import { blendReason, cardAlbumIds, cardColors, hourLabel, releaseDate, wrappedCards } from './wrapped';

const track = (id: string, albumId: string) => ({ id, title: `T${id}`, artist: 'A', albumId }) as never;

function sample(over: Partial<Wrapped> = {}): Wrapped {
  return {
    year: 2026,
    final: false,
    totals: { minutes: 1234, plays: 300, tracks: 80, artists: 20, albums: 30, days: 40 },
    topTracks: [{ track: track('1', 'al1'), plays: 20, minutes: 70 }],
    topArtists: [{ artist: { id: 'ar1', name: 'Band', albumCount: 1 } as never, artUrl: 'art/al2', plays: 50, minutes: 200 }],
    topAlbums: [{ album: { id: 'al3', title: 'LP', artist: 'Band' } as never, plays: 30, minutes: 100 }],
    topGenres: [{ name: 'Rock', minutes: 500, share: 0.4 }],
    byHour: Array(24).fill(1),
    byWeekday: Array(7).fill(1),
    byMonth: Array(12).fill(1),
    peakHour: 20,
    peakWeekday: 4,
    streaks: { longest: 5, current: 2 },
    discoveries: { artists: 3, tracks: 10, share: 0.1, topArtists: [] },
    aura: { label: 'Golden', description: 'x', energy: 0.5, moods: [], colors: ['#111111', '#222222'] },
    ...over,
  };
}

describe('wrapped cards', () => {
  it('builds the story in order and skips empty parts', () => {
    const cards = wrappedCards(sample());
    expect(cards.map((c) => c.kind)).toEqual(['intro', 'list', 'list', 'list', 'genres', 'clock', 'stat', 'stat', 'aura', 'summary']);
    expect(cards[0]).toMatchObject({ title: 'Your 2026 so far', big: '1,234' });
    const c = wrappedCards(sample({ final: true, aura: null, streaks: { longest: 1, current: 0 }, discoveries: { artists: 0, tracks: 0, share: 0, topArtists: [] } }));
    expect(c[0].title).toBe('Your 2026 Wrapped');
    expect(c.map((x) => x.kind)).not.toContain('aura');
    expect(c.filter((x) => x.kind === 'stat')).toHaveLength(0);
  });
  it('shows a single card with no plays', () => {
    expect(wrappedCards(sample({ totals: { minutes: 0, plays: 0, tracks: 0, artists: 0, albums: 0 } }))).toHaveLength(1);
  });
  it('collects cover album ids', () => {
    expect(cardAlbumIds(wrappedCards(sample())).sort()).toEqual(['al1', 'al2', 'al3']);
  });
  it('uses the aura colours, alternating', () => {
    expect(cardColors(sample(), 0)).toEqual(['#111111', '#222222']);
    expect(cardColors(sample(), 1)).toEqual(['#222222', '#111111']);
    expect(cardColors(sample({ aura: null }), 0)[0]).toBe('#4ade80');
  });
  it('formats hours and dates', () => {
    expect([0, 9, 12, 21].map(hourLabel)).toEqual(['midnight', '9 am', 'noon', '9 pm']);
    expect(releaseDate('2026-11-13')).toBe('13 Nov');
    expect(releaseDate('2026')).toBe('2026');
    expect(releaseDate(null)).toBe('');
  });
});

describe('blend attribution', () => {
  const names = { u1: 'alex', u2: 'sam' };
  it('words each reason', () => {
    expect(blendReason('top', ['u2'], names, 'u1')).toBe('Because sam plays it a lot');
    expect(blendReason('match', ['u2'], names, 'u1')).toBe('Because sam plays this');
    expect(blendReason('top', ['u1'], names, 'u1')).toBe('Because you play it a lot');
    expect(blendReason('shared', ['u1', 'u2'], names, 'u1')).toBe('You and sam both love this');
    expect(blendReason('shared', ['u1', 'u2', 'u3'], names)).toBe('3 of you love this');
    expect(blendReason('top', [], names)).toBe('');
  });
});
