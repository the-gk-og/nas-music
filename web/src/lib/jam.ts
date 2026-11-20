// Jam (listen together): types for api/openapi.yaml's Jam* schemas, position extrapolation and a small
// reconnecting client for /api/v1/jam/ws. Shared by the guest page (src/jam) and the host sheet.
// Pure TypeScript with injectable socket + timers so it is unit-tested.
import type { SocketLike } from './connect';

export interface JamTrack {
  id: string;
  title: string;
  artist: string;
  album: string;
  albumId: string;
  durationS: number;
}

export interface JamAdder {
  guestId: string | null;
  name: string;
  isHost: boolean;
}

export interface JamVotes {
  up: number;
  down: number;
  mine: -1 | 0 | 1;
}

export interface JamEntry {
  position: number;
  track: JamTrack;
  addedBy: JamAdder;
  votes: JamVotes;
}

export interface JamNowPlaying extends JamEntry {
  positionMs: number;
  positionAt: string;
  playing: boolean;
  speed: number;
}

export interface JamRequest {
  id: string;
  trackId: string;
  track: JamTrack | null;
  playNext: boolean;
  status: 'pending' | 'queued' | 'rejected' | 'withdrawn' | 'removed';
  createdAt: string;
  by: { guestId: string; name: string } | null;
}

export interface JamSettings {
  approval: boolean;
  allowPlayNext: boolean;
  maxPerGuest: number | null;
}

export interface JamSnapshot {
  jam: {
    id: string;
    hostName: string;
    settings: JamSettings;
    createdAt: string;
    expiresAt: string;
    ended: boolean;
    joinCode?: string | null;
    joinUrl?: string | null;
    lanJoinUrl?: string | null;
    joinExpiresAt?: string | null;
    queueId?: string;
  };
  serverTime: string;
  nowPlaying: JamNowPlaying | null;
  upcoming: JamEntry[];
  pending: JamRequest[];
  guests: { id: string; name: string; isMember: boolean }[];
  me: {
    guestId: string | null;
    name: string;
    isHost: boolean;
    isMember?: boolean;
    waiting?: number;
    remaining?: number | null;
    expiresAt?: string;
  };
}

export interface JamPreview {
  hostName: string;
  guestCount: number;
  linkExpiresAt: string;
  signedInAs: string | null;
}

export const REACTIONS = ['🔥', '❤️', '😂', '👏', '🕺', '🙌', '😮', '👎'] as const;

/** Server clock minus ours (ms), from a message's `serverTime`. */
export function clockOffset(serverTime: string | undefined, now = Date.now()): number {
  const t = serverTime ? Date.parse(serverTime) : NaN;
  return Number.isFinite(t) ? t - now : 0;
}

/** Where the host's playback is now (ms), extrapolated like Connect, corrected for clock skew. */
export function positionNow(np: Pick<JamNowPlaying, 'positionMs' | 'positionAt' | 'playing' | 'speed'> | null,
  offsetMs = 0, now = Date.now()): number {
  if (!np) return 0;
  if (!np.playing) return np.positionMs;
  const at = Date.parse(np.positionAt);
  if (!Number.isFinite(at)) return np.positionMs;
  return Math.max(0, np.positionMs + Math.max(0, now + offsetMs - at) * (np.speed || 1));
}

/** Apply a `playback` message (same track, new position / pause) to a snapshot. */
export function applyPlayback(s: JamSnapshot, msg: { nowPlaying: Partial<JamNowPlaying>; serverTime?: string }): JamSnapshot {
  if (!s.nowPlaying) return s;
  return { ...s, serverTime: msg.serverTime ?? s.serverTime, nowPlaying: { ...s.nowPlaying, ...msg.nowPlaying } };
}

export function fmtMs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** The code in a guest page URL: `/jam/<code>`. */
export function codeFromPath(path: string): string | null {
  const m = /^\/jam\/([A-Za-z0-9_-]{4,64})\/?$/.exec(path);
  return m ? m[1] : null;
}

export type JamEvent =
  | { type: 'snapshot'; snapshot: JamSnapshot }
  | { type: 'playback'; nowPlaying: Partial<JamNowPlaying>; serverTime?: string }
  | { type: 'reaction'; emoji: string; name: string }
  | { type: 'gone'; reason: 'kicked' | 'ended' | 'expired' };

export interface JamSocketOptions {
  url: string;
  onEvent: (e: JamEvent) => void;
  onStatus?: (open: boolean) => void;
  makeSocket?: (url: string) => SocketLike;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

/** Reconnects with backoff until the server says we're out (close 4401/4403/4410), then stops. */
export class JamSocket {
  private ws: SocketLike | null = null;
  private stopped = false;
  private attempt = 0;
  private timer: unknown = null;
  private pinger: unknown = null;
  private goneSent = false;

  constructor(private o: JamSocketOptions) {}

  start() {
    this.stopped = false;
    this.open();
  }

  stop() {
    this.stopped = true;
    if (this.timer) (this.o.clearTimer ?? clearTimeout)(this.timer as ReturnType<typeof setTimeout>);
    if (this.pinger) (this.o.clearTimer ?? clearTimeout)(this.pinger as ReturnType<typeof setTimeout>);
    this.ws?.close(1000, 'bye');
    this.ws = null;
  }

  private open() {
    const make = this.o.makeSocket ?? ((u: string) => new WebSocket(u) as unknown as SocketLike);
    const ws = (this.ws = make(this.o.url));
    ws.onopen = () => {
      this.attempt = 0;
      this.o.onStatus?.(true);
      this.ping();
    };
    ws.onmessage = (ev) => {
      let m: Record<string, unknown>;
      try {
        m = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if (m.type === 'snapshot') {
        const { type: _t, ...snap } = m;
        void _t;
        this.o.onEvent({ type: 'snapshot', snapshot: snap as unknown as JamSnapshot });
      } else if (m.type === 'playback' && m.nowPlaying && typeof m.nowPlaying === 'object') {
        this.o.onEvent({ type: 'playback', nowPlaying: m.nowPlaying as Partial<JamNowPlaying>, serverTime: m.serverTime as string });
      } else if (m.type === 'reaction' && typeof m.emoji === 'string' && typeof m.name === 'string') {
        this.o.onEvent({ type: 'reaction', emoji: m.emoji, name: m.name });
      } else if (m.type === 'kicked') {
        this.gone('kicked');
      } else if (m.type === 'ended') {
        this.gone('ended');
      }
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.o.onStatus?.(false);
      const code = (ev as { code?: number } | null)?.code;
      if (code === 4403) return this.gone('kicked');
      if (code === 4410) return this.gone('ended');
      if (code === 4401) return this.gone('expired');
      if (this.stopped) return;
      const delay = Math.min(30_000, 1000 * 2 ** this.attempt++);
      this.timer = (this.o.setTimer ?? setTimeout)(() => !this.stopped && this.open(), delay);
    };
    ws.onerror = () => {};
  }

  private ping() {
    this.pinger = (this.o.setTimer ?? setTimeout)(() => {
      if (this.stopped || !this.ws) return;
      if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: 'ping' }));
      this.ping();
    }, 25_000);
  }

  private gone(reason: 'kicked' | 'ended' | 'expired') {
    this.stopped = true;
    if (this.goneSent) return;
    this.goneSent = true;
    this.o.onEvent({ type: 'gone', reason });
  }
}
