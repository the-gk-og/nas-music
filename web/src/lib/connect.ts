// Connect client (§5): one WebSocket per client, reconnect with backoff, keep-alive, rev-ordered state.
// Pure TypeScript with injectable socket + timers so the state machine is unit-tested.
import type {
  ClientMessage,
  CommandAction,
  ConnectDevice,
  DeviceKind,
  PlaybackState,
  ServerMessage,
} from './types';

export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export type ConnectStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface ConnectHandlers {
  /** This device became active: load state.queueId, seek to positionMs, play if state.playing. */
  onTakeover?(state: PlaybackState): void;
  /** No longer active: stop audio, keep UI as remote. */
  onRelease?(): void;
  /** A remote asked the active device (us) to do something. */
  onCommand?(action: CommandAction, args: Record<string, unknown>, fromDeviceId?: string): void;
  onState?(state: PlaybackState): void;
  onDevices?(devices: ConnectDevice[]): void;
  onQueueChanged?(queueId: string): void;
  onStatus?(status: ConnectStatus): void;
}

export interface ConnectOptions {
  url: () => string;
  hello: { deviceName: string; kind: DeviceKind; canPlay: boolean; appVersion: string };
  socketFactory: (url: string) => SocketLike;
  handlers?: ConnectHandlers;
  timers?: {
    setTimeout: (fn: () => void, ms: number) => unknown;
    clearTimeout: (h: unknown) => void;
    setInterval: (fn: () => void, ms: number) => unknown;
    clearInterval: (h: unknown) => void;
  };
  pingMs?: number;
  pongTimeoutMs?: number;
  minBackoffMs?: number;
  maxBackoffMs?: number;
  now?: () => number;
}

const OPEN = 1;

/** Position now, extrapolating from positionAt while playing. */
export function extrapolatePosition(s: PlaybackState, nowMs: number): number {
  if (!s.playing) return s.positionMs;
  const at = Date.parse(s.positionAt);
  if (Number.isNaN(at)) return s.positionMs;
  return Math.max(0, s.positionMs + (nowMs - at) * (s.speed || 1));
}

export function backoffMs(attempt: number, min = 1000, max = 30000): number {
  return Math.min(max, min * Math.pow(2, Math.max(0, attempt)));
}

export class ConnectClient {
  status: ConnectStatus = 'idle';
  deviceId: string | null = null;
  devices: ConnectDevice[] = [];
  state: PlaybackState | null = null;

  private ws: SocketLike | null = null;
  private attempt = 0;
  private stopped = true;
  private reconnectTimer: unknown = null;
  private pingTimer: unknown = null;
  private pongTimer: unknown = null;
  private pendingState: PlaybackState | null = null;
  private opts: Required<Pick<ConnectOptions, 'pingMs' | 'pongTimeoutMs' | 'minBackoffMs' | 'maxBackoffMs'>> &
    ConnectOptions;
  private timers: NonNullable<ConnectOptions['timers']>;
  handlers: ConnectHandlers;

  constructor(opts: ConnectOptions) {
    this.opts = { pingMs: 25000, pongTimeoutMs: 10000, minBackoffMs: 1000, maxBackoffMs: 30000, ...opts };
    this.handlers = opts.handlers ?? {};
    this.timers = opts.timers ?? {
      setTimeout: (f, ms) => setTimeout(f, ms),
      clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
      setInterval: (f, ms) => setInterval(f, ms),
      clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
    };
  }

  /** True when this client is the user's active (audio-playing) device. */
  get isActive(): boolean {
    return !!this.deviceId && this.state?.activeDeviceId === this.deviceId;
  }

  /** The other device that's playing, if any ("Playing on X"). */
  get remoteActive(): ConnectDevice | null {
    const id = this.state?.activeDeviceId;
    if (!id || id === this.deviceId) return null;
    return this.devices.find((d) => d.id === id) ?? null;
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.open();
  }

  stop() {
    this.stopped = true;
    this.clearTimers();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close(1000, 'bye');
      this.ws = null;
    }
    this.setStatus('closed');
  }

  private setStatus(s: ConnectStatus) {
    if (this.status === s) return;
    this.status = s;
    this.handlers.onStatus?.(s);
  }

  private open() {
    this.setStatus(this.attempt ? 'reconnecting' : 'connecting');
    let ws: SocketLike;
    try {
      ws = this.opts.socketFactory(this.opts.url());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.send({ type: 'hello', ...this.opts.hello });
    };
    ws.onmessage = (ev) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      this.handle(msg);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
    ws.onclose = () => {
      this.ws = null;
      this.clearTimers();
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    this.setStatus('reconnecting');
    const delay = backoffMs(this.attempt, this.opts.minBackoffMs, this.opts.maxBackoffMs);
    this.attempt++;
    this.reconnectTimer = this.timers.setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.stopped) this.open();
    }, delay);
  }

  /** Force an immediate reconnect (e.g. on `online` / visibility change). */
  kick() {
    if (this.stopped || this.status === 'open' || this.status === 'connecting') return;
    if (this.reconnectTimer) this.timers.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.open();
  }

  private clearTimers() {
    if (this.reconnectTimer) this.timers.clearTimeout(this.reconnectTimer);
    if (this.pingTimer) this.timers.clearInterval(this.pingTimer);
    if (this.pongTimer) this.timers.clearTimeout(this.pongTimer);
    this.reconnectTimer = this.pingTimer = this.pongTimer = null;
  }

  private startPing() {
    if (this.pingTimer) this.timers.clearInterval(this.pingTimer);
    this.pingTimer = this.timers.setInterval(() => {
      this.send({ type: 'ping' });
      if (!this.pongTimer)
        this.pongTimer = this.timers.setTimeout(() => {
          // Dead connection (e.g. tunnel dropped silently): reconnect.
          this.pongTimer = null;
          this.ws?.close(4000, 'pong timeout');
        }, this.opts.pongTimeoutMs);
    }, this.opts.pingMs);
  }

  private send(msg: ClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== OPEN) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  /** Accept a state if it is newer (rev-ordered). */
  private acceptState(s: PlaybackState | null | undefined): boolean {
    if (!s) return false;
    if (this.state && typeof s.rev === 'number' && typeof this.state.rev === 'number' && s.rev < this.state.rev) return false;
    const wasActive = this.isActive;
    this.state = s;
    this.handlers.onState?.(s);
    // Another device took over without a `release` reaching us (e.g. we were offline).
    if (wasActive && !this.isActive) this.handlers.onRelease?.();
    return true;
  }

  handle(msg: ServerMessage) {
    switch (msg.type) {
      case 'welcome': {
        this.attempt = 0;
        this.deviceId = msg.deviceId;
        this.devices = msg.devices ?? [];
        this.setStatus('open');
        this.startPing();
        this.handlers.onDevices?.(this.devices);
        const prev = this.pendingState;
        this.state = null; // server is authoritative after (re)connect
        if (msg.state) this.acceptState(msg.state);
        // We were active and kept playing while offline: re-assert our state.
        if (prev && msg.state?.activeDeviceId === this.deviceId) this.reportState(prev);
        break;
      }
      case 'devices':
        this.devices = msg.devices ?? [];
        this.handlers.onDevices?.(this.devices);
        break;
      case 'state': {
        const s = 'state' in msg && msg.state ? msg.state : (msg as PlaybackState);
        this.acceptState(s);
        break;
      }
      case 'takeover':
        this.state = { ...msg.state, activeDeviceId: this.deviceId };
        this.handlers.onState?.(this.state);
        this.handlers.onTakeover?.(msg.state);
        break;
      case 'release':
        if (this.state && this.state.activeDeviceId === this.deviceId) this.state = { ...this.state, activeDeviceId: null };
        this.pendingState = null;
        this.handlers.onRelease?.();
        break;
      case 'command':
        this.handlers.onCommand?.(msg.action, msg.args ?? {}, msg.fromDeviceId);
        break;
      case 'queues':
        this.handlers.onQueueChanged?.(msg.queueId);
        break;
      case 'pong':
        if (this.pongTimer) this.timers.clearTimeout(this.pongTimer);
        this.pongTimer = null;
        break;
    }
  }

  /** Active device only: report the playback state. Kept and re-sent after a reconnect. */
  reportState(s: PlaybackState) {
    this.pendingState = s;
    this.state = { ...s, rev: Math.max(s.rev ?? 0, this.state?.rev ?? 0) };
    this.send({ type: 'state', ...s });
  }

  command(action: CommandAction, args: Record<string, unknown> = {}, targetDeviceId?: string): boolean {
    return this.send({ type: 'command', action, args, ...(targetDeviceId ? { targetDeviceId } : {}) });
  }

  transfer(toDeviceId: string, play: boolean): boolean {
    return this.send({ type: 'transfer', toDeviceId, play });
  }

  /** Starting playback locally: claim the active role first (§5 rule 1). */
  claim(play = true): boolean {
    if (!this.deviceId || this.isActive) return false;
    const ok = this.transfer(this.deviceId, play);
    if (ok && this.state) this.state = { ...this.state, activeDeviceId: this.deviceId };
    return ok;
  }
}
