import { describe, expect, it } from 'vitest';
import type { SocketLike } from './connect';
import { applyPlayback, clockOffset, codeFromPath, fmtMs, JamSocket, positionNow, type JamEvent, type JamSnapshot } from './jam';

class FakeSocket implements SocketLike {
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: SocketLike['onopen'] = null;
  onmessage: SocketLike['onmessage'] = null;
  onclose: SocketLike['onclose'] = null;
  onerror: SocketLike['onerror'] = null;
  constructor(public url: string) {}
  send(d: string) {
    this.sent.push(JSON.parse(d));
  }
  close(code?: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  recv(m: unknown) {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
}

function harness() {
  const sockets: FakeSocket[] = [];
  const events: JamEvent[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const s = new JamSocket({
    url: 'wss://x/api/v1/jam/ws?token=jg_a',
    onEvent: (e) => events.push(e),
    makeSocket: (u) => {
      const f = new FakeSocket(u);
      sockets.push(f);
      return f;
    },
    setTimer: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimer: () => {},
  });
  return { s, sockets, events, timers };
}

describe('jam helpers', () => {
  it('extrapolates position with clock offset and speed', () => {
    const np = { positionMs: 10_000, positionAt: '2026-01-01T00:00:00.000Z', playing: true, speed: 1 };
    const now = Date.parse('2026-01-01T00:00:05.000Z');
    expect(positionNow(np, 0, now)).toBe(15_000);
    // Our clock is 2 s behind the server's: the server is already 2 s further on.
    expect(positionNow(np, 2000, now)).toBe(17_000);
    expect(positionNow({ ...np, speed: 2 }, 0, now)).toBe(20_000);
    expect(positionNow({ ...np, playing: false }, 0, now)).toBe(10_000);
    expect(positionNow(null)).toBe(0);
    expect(clockOffset('2026-01-01T00:00:07.000Z', now)).toBe(2000);
    expect(clockOffset(undefined, now)).toBe(0);
  });

  it('applies playback messages and parses codes', () => {
    const snap = { nowPlaying: { positionMs: 0, playing: true }, serverTime: 'a' } as unknown as JamSnapshot;
    const out = applyPlayback(snap, { nowPlaying: { positionMs: 5000, playing: false }, serverTime: 'b' });
    expect(out.nowPlaying).toMatchObject({ positionMs: 5000, playing: false });
    expect(out.serverTime).toBe('b');
    expect(applyPlayback({ nowPlaying: null } as unknown as JamSnapshot, { nowPlaying: {} }).nowPlaying).toBeNull();
    expect(codeFromPath('/jam/AbC_12-xyz')).toBe('AbC_12-xyz');
    expect(codeFromPath('/jam/../etc')).toBeNull();
    expect(codeFromPath('/join/abcd')).toBeNull();
    expect(fmtMs(125_000)).toBe('2:05');
  });
});

describe('JamSocket', () => {
  it('delivers snapshots, reactions and reconnects with backoff', () => {
    const { s, sockets, events, timers } = harness();
    s.start();
    sockets[0].open();
    sockets[0].recv({ type: 'snapshot', jam: { id: 'j' }, upcoming: [] });
    sockets[0].recv({ type: 'reaction', emoji: '🔥', name: 'Sam' });
    sockets[0].recv({ type: 'playback', nowPlaying: { positionMs: 3 }, serverTime: 't' });
    sockets[0].recv({ type: 'mystery' });
    expect(events.map((e) => e.type)).toEqual(['snapshot', 'reaction', 'playback']);
    expect((events[0] as { snapshot: JamSnapshot }).snapshot.jam.id).toBe('j');
    sockets[0].close(1006);
    const retry = timers.find((t) => t.ms === 1000);
    expect(retry).toBeTruthy();
    retry!.fn();
    expect(sockets).toHaveLength(2);
  });

  it('stops for good when kicked, ended or expired', () => {
    for (const [code, reason] of [[4403, 'kicked'], [4410, 'ended'], [4401, 'expired']] as const) {
      const { s, sockets, events, timers } = harness();
      s.start();
      sockets[0].open();
      sockets[0].close(code);
      expect(events).toEqual([{ type: 'gone', reason }]);
      expect(timers.filter((t) => t.ms < 25_000)).toHaveLength(0);
    }
    const { s, sockets, events } = harness();
    s.start();
    sockets[0].open();
    sockets[0].recv({ type: 'ended' });
    sockets[0].close(4410);
    expect(events).toEqual([{ type: 'gone', reason: 'ended' }]);
  });
});
