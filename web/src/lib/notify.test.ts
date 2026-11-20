import { describe, expect, it } from 'vitest';
import { mergeNotifications, newestAt, scrobbleLine } from './notify';
import type { AppNotification, ScrobbleService } from './types';

const n = (id: string, createdAt: string, read = false): AppNotification => ({
  id, kind: 'new_music', title: 't', body: 'b', data: {}, createdAt, read,
});

describe('notifications', () => {
  it('merges by id, newest first', () => {
    const out = mergeNotifications([n('a', '2026-01-01T00:00:00Z'), n('b', '2026-01-02T00:00:00Z')], [n('a', '2026-01-01T00:00:00Z', true), n('c', '2026-01-03T00:00:00Z')]);
    expect(out.map((x) => x.id)).toEqual(['c', 'b', 'a']);
    expect(out[2].read).toBe(true);
    expect(newestAt(out)).toBe('2026-01-03T00:00:00Z');
    expect(newestAt([])).toBeUndefined();
    expect(mergeNotifications(out, [], 2).length).toBe(2);
  });
});

describe('scrobble status line', () => {
  const base: ScrobbleService = { service: 'listenbrainz', available: true, connected: true, enabled: true, username: 'me', nowPlaying: true, needsReauth: false, pending: 0, failed: 0, sent: 3 };
  it('describes each state', () => {
    expect(scrobbleLine({ ...base, connected: false })).toBe('Not connected');
    expect(scrobbleLine({ ...base, connected: false, available: false })).toBe('Not set up on this server');
    expect(scrobbleLine({ ...base, needsReauth: true, pending: 2 })).toContain('reconnect (2 waiting)');
    expect(scrobbleLine({ ...base, enabled: false })).toBe('Connected as me · paused');
    expect(scrobbleLine({ ...base, lastScrobbleAt: 'x', pending: 1, failed: 2 }, () => 'now')).toBe('Connected as me · last scrobble now · 1 waiting · 2 failed');
  });
});
