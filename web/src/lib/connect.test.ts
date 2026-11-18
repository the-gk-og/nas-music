import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectClient, backoffMs, extrapolatePosition, type SocketLike } from './connect';
import type { PlaybackState } from './types';

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
  close() {
    this.readyState = 3;
    this.onclose?.({});
  }
  // test helpers
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  recv(m: unknown) {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
}

const st = (p: Partial<PlaybackState> = {}): PlaybackState => ({
  activeDeviceId: null,
  queueId: 'q1',
  index: 0,
  trackId: 't1',
  positionMs: 0,
  positionAt: '2026-11-18T09:00:00.000Z',
  playing: false,
  volume: 1,
  shuffle: false,
  repeat: 'off',
  speed: 1,
  rev: 1,
  ...p,
});

let sockets: FakeSocket[];
let handlers: Record<string, ReturnType<typeof vi.fn>>;
let c: ConnectClient;

beforeEach(() => {
  vi.useFakeTimers();
  sockets = [];
  handlers = {
    onTakeover: vi.fn(),
    onRelease: vi.fn(),
    onCommand: vi.fn(),
    onState: vi.fn(),
    onDevices: vi.fn(),
    onQueueChanged: vi.fn(),
    onStatus: vi.fn(),
  };
  c = new ConnectClient({
    url: () => 'wss://x/api/v1/connect?token=abc',
    hello: { deviceName: 'Firefox on Linux', kind: 'web', canPlay: true, appVersion: '0.1.0' },
    socketFactory: (u) => {
      const s = new FakeSocket(u);
      sockets.push(s);
      return s;
    },
    handlers,
  });
});

function connect(state: PlaybackState | null = st()) {
  c.start();
  const s = sockets[sockets.length - 1];
  s.open();
  s.recv({ type: 'welcome', deviceId: 'me', devices: [{ id: 'me', name: 'web', kind: 'web', canPlay: true, online: true, isActive: false }, { id: 'phone', name: 'Pixel', kind: 'android', canPlay: true, online: true, isActive: true }], state });
  return s;
}

describe('ConnectClient', () => {
  it('says hello first and goes open on welcome', () => {
    const s = connect();
    expect(s.sent[0]).toEqual({ type: 'hello', deviceName: 'Firefox on Linux', kind: 'web', canPlay: true, appVersion: '0.1.0' });
    expect(c.status).toBe('open');
    expect(c.deviceId).toBe('me');
    expect(handlers.onDevices).toHaveBeenCalled();
  });

  it('is a remote while another device is active', () => {
    connect(st({ activeDeviceId: 'phone', playing: true }));
    expect(c.isActive).toBe(false);
    expect(c.remoteActive?.name).toBe('Pixel');
  });

  it('ignores out-of-order state by rev', () => {
    const s = connect(st({ rev: 5 }));
    s.recv({ type: 'state', ...st({ rev: 4, index: 9 }) });
    expect(c.state?.index).toBe(0);
    s.recv({ type: 'state', ...st({ rev: 6, index: 2 }) });
    expect(c.state?.index).toBe(2);
    // wrapped form also accepted
    s.recv({ type: 'state', state: st({ rev: 7, index: 3 }) });
    expect(c.state?.index).toBe(3);
  });

  it('takeover makes us active and hands the state to the player', () => {
    const s = connect(st({ activeDeviceId: 'phone' }));
    s.recv({ type: 'takeover', state: st({ activeDeviceId: 'me', positionMs: 61234, playing: true, rev: 9 }) });
    expect(c.isActive).toBe(true);
    expect(handlers.onTakeover).toHaveBeenCalledWith(expect.objectContaining({ positionMs: 61234, playing: true }));
  });

  it('release stops being active', () => {
    const s = connect(st({ activeDeviceId: 'me' }));
    expect(c.isActive).toBe(true);
    s.recv({ type: 'release' });
    expect(c.isActive).toBe(false);
    expect(handlers.onRelease).toHaveBeenCalled();
  });

  it('a newer state naming another device implies release', () => {
    const s = connect(st({ activeDeviceId: 'me', rev: 1 }));
    s.recv({ type: 'state', ...st({ activeDeviceId: 'phone', rev: 2 }) });
    expect(handlers.onRelease).toHaveBeenCalledTimes(1);
  });

  it('claims the active role with transfer-to-self before local playback', () => {
    const s = connect(st({ activeDeviceId: 'phone' }));
    expect(c.claim(true)).toBe(true);
    expect(s.sent.at(-1)).toEqual({ type: 'transfer', toDeviceId: 'me', play: true });
    expect(c.isActive).toBe(true);
    expect(c.claim(true)).toBe(false); // already active
  });

  it('sends commands and transfers', () => {
    const s = connect();
    c.command('seek', { positionMs: 1000 }, 'phone');
    c.transfer('phone', true);
    expect(s.sent.slice(-2)).toEqual([
      { type: 'command', action: 'seek', args: { positionMs: 1000 }, targetDeviceId: 'phone' },
      { type: 'transfer', toDeviceId: 'phone', play: true },
    ]);
  });

  it('delivers commands and queue changes', () => {
    const s = connect();
    s.recv({ type: 'command', action: 'next', fromDeviceId: 'phone' });
    s.recv({ type: 'queues', queueId: 'q7' });
    expect(handlers.onCommand).toHaveBeenCalledWith('next', {}, 'phone');
    expect(handlers.onQueueChanged).toHaveBeenCalledWith('q7');
  });

  it('pings every 25 s and reconnects when no pong arrives', () => {
    const s = connect();
    vi.advanceTimersByTime(25000);
    expect(s.sent.at(-1)).toEqual({ type: 'ping' });
    s.recv({ type: 'pong' });
    vi.advanceTimersByTime(25000 + 10000);
    expect(s.readyState).toBe(3);
    expect(c.status).toBe('reconnecting');
    vi.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(2);
  });

  it('reconnects with exponential backoff and re-asserts state when still active', () => {
    const s = connect(st({ activeDeviceId: 'me' }));
    c.reportState(st({ activeDeviceId: 'me', positionMs: 5000, playing: true, rev: 3 }));
    s.close();
    expect(c.status).toBe('reconnecting');
    vi.advanceTimersByTime(999);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);
    sockets[1].close(); // fails again → 2 s
    vi.advanceTimersByTime(1999);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    const s3 = sockets[2];
    s3.open();
    s3.recv({ type: 'welcome', deviceId: 'me', devices: [], state: st({ activeDeviceId: 'me', playing: false, rev: 4 }) });
    expect(s3.sent.map((m) => m.type)).toEqual(['hello', 'state']);
    expect(s3.sent[1]).toMatchObject({ positionMs: 5000, playing: true });
  });

  it('stop() closes without reconnecting', () => {
    connect();
    c.stop();
    vi.advanceTimersByTime(60000);
    expect(sockets).toHaveLength(1);
    expect(c.status).toBe('closed');
  });
});

it('backoff doubles up to the cap', () => {
  expect([0, 1, 2, 3, 10].map((a) => backoffMs(a))).toEqual([1000, 2000, 4000, 8000, 30000]);
});

it('extrapolates position while playing', () => {
  const s = st({ positionMs: 1000, positionAt: '2026-11-18T09:00:00.000Z', playing: true, speed: 1.5 });
  expect(extrapolatePosition(s, Date.parse('2026-11-18T09:00:02.000Z'))).toBe(4000);
  expect(extrapolatePosition({ ...s, playing: false }, Date.parse('2026-11-18T09:00:02.000Z'))).toBe(1000);
});
