import { describe, expect, it, vi } from 'vitest';
import { EventQueue, PlaybackTracker, buildContext } from './events';
import type { PlayEvent, Track } from './types';

const tr = (id: string, d = 100): Track => ({ id, title: id, artist: '', artistId: '', album: '', albumId: '', durationS: d, starred: false });

describe('PlaybackTracker', () => {
  it('emits play once at 80 %', () => {
    const out: PlayEvent[] = [];
    const p = new PlaybackTracker((e) => out.push(e));
    p.start(tr('a'));
    for (let s = 0; s <= 90; s += 2) p.progress(s);
    p.ended();
    expect(out.map((e) => e.type)).toEqual(['play']);
    expect(out[0].positionS).toBe(80);
  });
  it('emits skip with position when moving on early', () => {
    const out: PlayEvent[] = [];
    const p = new PlaybackTracker((e) => out.push(e));
    p.start(tr('a'), { source: 'search' });
    p.progress(12.34);
    p.start(tr('b'));
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ type: 'skip', trackId: 'a', positionS: 12.3, source: 'search' });
  });
  it('records seeks and repeat-of-previous', () => {
    const details: unknown[] = [];
    const p = new PlaybackTracker((_e, d) => details.push(d));
    p.start(tr('a'));
    p.seek(10, 50);
    p.ended();
    p.start(tr('a'));
    p.finish(3);
    expect(details[0]).toMatchObject({ seeks: [{ fromMs: 10000, toMs: 50000 }], repeatOfPrevious: false });
    expect(details[1]).toMatchObject({ repeatOfPrevious: true, sessionPosition: 2 });
  });
});

describe('EventQueue', () => {
  it('batches, persists and retries', async () => {
    const mem = new Map<string, string>();
    const storage = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) };
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({});
    const q = new EventQueue(send, storage, 100);
    q.push({ id: '1', type: 'play', trackId: 'a', at: 'x' });
    await q.flush();
    expect(q.size).toBe(1);
    // survives a reload
    const q2 = new EventQueue(send, storage, 100);
    expect(q2.size).toBe(1);
    await q2.flush();
    expect(q2.size).toBe(0);
    expect(send.mock.calls[1][0][0].offline).toBe(true);
  });
});

describe('buildContext', () => {
  const details = { seeks: [{ fromMs: 1000, toMs: 2000 }], volumeChanges: [], repeatOfPrevious: false, sessionPosition: 3, meta: { searchQuery: 'conver', addedFrom: 'search' } };
  const env = { output: 'moto buds', deviceKind: 'web' as const, network: 'wifi' as const, now: new Date(2026, 10, 18, 21, 5) };
  it('is empty when every switch is off', () => {
    expect(buildContext({}, details, env)).toBeUndefined();
  });
  it('includes only enabled signals', () => {
    const c = buildContext({ search: true, time: true }, details, env)!;
    expect(c).toEqual({ searchQuery: 'conver', localHour: 21, weekday: 3 });
    const all = buildContext({ listening: true, output: true, device: true }, details, env)!;
    expect(all).toMatchObject({ seeks: [{ fromMs: 1000, toMs: 2000 }], addedFrom: 'search', sessionPosition: 3, output: 'moto buds', deviceKind: 'web', network: 'wifi' });
  });
});
