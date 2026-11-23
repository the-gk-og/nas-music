import { describe, expect, it } from 'vitest';
import { preferServer, resumeQueueId, resumeSpot } from './resume';
import type { PlaybackState } from './types';

const tracks = ['a', 'b', 'c', 'd'].map((id) => ({ id }));

describe('resume where you left off', () => {
  it('reopens the queue this browser played, else the most recently used one (not slot 1)', () => {
    const qs = [
      { id: 'q1', updatedAt: '2026-11-01T00:00:00Z' },
      { id: 'q2', updatedAt: '2026-11-20T00:00:00Z' },
      { id: 'q3', updatedAt: '2026-11-10T00:00:00Z' },
    ];
    expect(resumeQueueId(qs, { queueId: 'q3', index: 0, positionS: 0 })).toBe('q3');
    expect(resumeQueueId(qs, { queueId: 'gone', index: 0, positionS: 0 })).toBe('q2');
    expect(resumeQueueId(qs, null)).toBe('q2');
    expect(resumeQueueId([], null)).toBeNull();
  });

  it('keeps the song and position, never the top of the playlist', () => {
    const q = { id: 'q', tracks, index: 0, positionMs: 0 };
    expect(resumeSpot(q, { queueId: 'q', index: 2, positionS: 61, trackId: 'c' })).toEqual({ index: 2, positionS: 61 });
    // the queue was reordered elsewhere: find the song again
    expect(resumeSpot({ ...q, tracks: [{ id: 'c' }, ...tracks.filter((t) => t.id !== 'c')] }, { queueId: 'q', index: 2, positionS: 5, trackId: 'c' }))
      .toEqual({ index: 0, positionS: 5 });
    // another device's queue: the server's saved spot
    expect(resumeSpot({ ...q, index: 3, positionMs: 42_000 }, { queueId: 'other', index: 1, positionS: 9 })).toEqual({ index: 3, positionS: 42 });
    expect(resumeSpot({ ...q, index: 3, positionMs: 42_000 }, null)).toEqual({ index: 3, positionS: 42 });
    expect(resumeSpot({ ...q, tracks: [] }, null)).toEqual({ index: 0, positionS: 0 });
  });

  it('picks up a newer paused state from another device', () => {
    const base: PlaybackState = {
      activeDeviceId: 'phone', queueId: 'q', index: 0, trackId: 't', positionMs: 0, positionAt: new Date(100_000).toISOString(),
      playing: false, volume: 1, shuffle: false, repeat: 'off', speed: 1, rev: 1,
    };
    expect(preferServer(10_000, base, 'web')).toBe(true);
    expect(preferServer(null, base, 'web')).toBe(true);
    expect(preferServer(99_000, base, 'web')).toBe(false); // within the slack
    expect(preferServer(200_000, base, 'web')).toBe(false); // ours is newer
    expect(preferServer(10_000, { ...base, playing: true }, 'web')).toBe(false);
    expect(preferServer(10_000, { ...base, activeDeviceId: 'web' }, 'web')).toBe(false);
    expect(preferServer(10_000, { ...base, queueId: null }, 'web')).toBe(false);
    expect(preferServer(10_000, { ...base, queueId: null, media: { kind: 'episode', id: 'e' } }, 'web')).toBe(true);
  });
});

describe('continue listening cards', () => {
  it('shows an episode by how much was listened, a queue by its song', async () => {
    const { progressFraction } = await import('./v3');
    expect(progressFraction({ fraction: 0.1612 })).toBeCloseTo(0.1612);
    expect(progressFraction({ index: 1, count: 4 })).toBe(0.25);
    expect(progressFraction({})).toBeNull();
  });
});
