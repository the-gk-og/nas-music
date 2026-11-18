// Listening events (play ≥ 80 % or end, skip with positionS) + batching to POST /events.
import type { EventContext, EventSource, PlayEvent, Track } from './types';

export const PLAY_THRESHOLD = 0.8;

export function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export interface PlayMeta {
  source?: EventSource;
  mixId?: string | null;
  addedFrom?: string;
  searchQuery?: string;
}

/** Collects listening details for one playthrough and decides play vs skip. */
export class PlaybackTracker {
  private track: Track | null = null;
  private meta: PlayMeta = {};
  private maxPos = 0;
  private lastPos = 0;
  private counted = false;
  private seeks: { fromMs: number; toMs: number }[] = [];
  private volumeChanges: { atMs: number; volume: number }[] = [];
  private lastTrackId: string | null = null;
  private sessionPosition = 0;

  constructor(
    private emit: (e: PlayEvent, details: { seeks: PlaybackTracker['seeks']; volumeChanges: PlaybackTracker['volumeChanges']; repeatOfPrevious: boolean; sessionPosition: number; meta: PlayMeta }) => void,
    private now: () => Date = () => new Date(),
  ) {}

  get current(): Track | null {
    return this.track;
  }

  /** New track started. Closes the previous one as a skip if it wasn't counted. */
  start(track: Track, meta: PlayMeta = {}) {
    this.finish(this.lastPos);
    this.lastTrackId = this.track?.id ?? this.lastTrackId;
    this.track = track;
    this.meta = meta;
    this.maxPos = this.lastPos = 0;
    this.counted = false;
    this.seeks = [];
    this.volumeChanges = [];
    this.sessionPosition++;
  }

  /** Periodic position update (seconds). Emits `play` once when crossing 80 %. */
  progress(posS: number, durationS?: number) {
    if (!this.track) return;
    this.lastPos = posS;
    // Only count continuous listening, not seeks forward.
    if (posS > this.maxPos && posS - this.maxPos < 5) this.maxPos = posS;
    const dur = durationS || this.track.durationS;
    if (!this.counted && dur > 0 && posS >= dur * PLAY_THRESHOLD) this.emitPlay(posS, dur);
  }

  seek(fromS: number, toS: number) {
    if (!this.track) return;
    this.seeks.push({ fromMs: Math.round(fromS * 1000), toMs: Math.round(toS * 1000) });
    this.lastPos = toS;
    if (toS < this.maxPos) this.maxPos = toS;
  }

  volume(atS: number, volume: number) {
    if (!this.track) return;
    const last = this.volumeChanges[this.volumeChanges.length - 1];
    const atMs = Math.round(atS * 1000);
    if (last && atMs - last.atMs < 1000) last.volume = Math.round(volume * 100) / 100;
    else this.volumeChanges.push({ atMs, volume: Math.round(volume * 100) / 100 });
  }

  /** Track reached its end naturally. */
  ended() {
    if (!this.track) return;
    if (!this.counted) this.emitPlay(this.track.durationS, this.track.durationS);
    this.lastTrackId = this.track.id;
    this.track = null;
  }

  /** User moved away (next/prev/another track/stop/transfer). Skip if not counted yet. */
  finish(posS: number = this.lastPos) {
    if (!this.track) return;
    if (!this.counted) {
      this.emitEvent('skip', posS, this.track.durationS);
    }
    this.lastTrackId = this.track.id;
    this.track = null;
  }

  private emitPlay(posS: number, dur: number) {
    this.counted = true;
    this.emitEvent('play', posS, dur);
  }

  private emitEvent(type: 'play' | 'skip', posS: number, dur: number) {
    const t = this.track!;
    this.emit(
      {
        id: uuid(),
        type,
        trackId: t.id,
        at: this.now().toISOString(),
        positionS: round1(posS),
        durationS: dur,
        source: this.meta.source ?? 'library',
        mixId: this.meta.mixId ?? null,
      },
      {
        seeks: this.seeks.slice(),
        volumeChanges: this.volumeChanges.slice(),
        repeatOfPrevious: this.lastTrackId === t.id,
        sessionPosition: this.sessionPosition,
        meta: this.meta,
      },
    );
  }
}

const round1 = (n: number) => Math.round(n * 10) / 10;

// ---- batching ----

const STORE_KEY = 'nm.events.pending';

export class EventQueue {
  private pending: PlayEvent[] = [];
  private flushing = false;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private send: (events: PlayEvent[]) => Promise<unknown>,
    private storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof localStorage !== 'undefined' ? localStorage : null,
    private batchSize = 20,
  ) {
    try {
      const raw = this.storage?.getItem(STORE_KEY);
      if (raw) this.pending = JSON.parse(raw);
    } catch {
      this.pending = [];
    }
  }

  get size() {
    return this.pending.length;
  }

  push(e: PlayEvent) {
    this.pending.push(e);
    this.persist();
    if (this.pending.length >= this.batchSize) void this.flush();
  }

  private persist() {
    try {
      this.storage?.setItem(STORE_KEY, JSON.stringify(this.pending.slice(-5000)));
    } catch {
      /* ignore */
    }
  }

  async flush(): Promise<void> {
    if (this.flushing || !this.pending.length) return;
    this.flushing = true;
    const batch = this.pending.slice(0, 1000);
    try {
      await this.send(batch);
      const ids = new Set(batch.map((e) => e.id));
      this.pending = this.pending.filter((e) => !ids.has(e.id));
      this.persist();
    } catch {
      // Keep for the next try; mark as offline-recorded.
      for (const e of batch) e.offline = true;
      this.persist();
    } finally {
      this.flushing = false;
    }
  }

  startTimer(ms = 30000) {
    this.stopTimer();
    this.timer = setInterval(() => void this.flush(), ms);
  }

  stopTimer() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

// ---- context (§10) ----

export type SignalKey = 'listening' | 'output' | 'device' | 'search' | 'time';

/** Signals that make sense in a browser. Movement/location/weather/watch are Android-only. */
export const WEB_SIGNALS: { key: SignalKey; label: string; explain: string }[] = [
  {
    key: 'listening',
    label: 'Listening details',
    explain: 'Seeks, volume changes, repeats, where a song was started from and its place in this session. Used to tell real skips from browsing.',
  },
  {
    key: 'output',
    label: 'Audio output',
    explain: 'The name of the speaker or headphones (when the browser shares it). Used for output-aware picks.',
  },
  {
    key: 'device',
    label: 'Device & network',
    explain: 'Device type, network type, battery and whether the tab is visible. Used to learn when and how you listen.',
  },
  {
    key: 'search',
    label: 'Search history',
    explain: 'The search text, on plays that started from a search. Used to connect what you look for with what you play.',
  },
  {
    key: 'time',
    label: 'Time & calendar context',
    explain: 'Local hour and weekday from this device’s clock (no calendar access). Used for time-of-day mixes.',
  },
];

/** Server settings key for a signal switch (shared with Android: `context.<key>`). */
export const signalSetting = (k: SignalKey) => `context.${k}`;

export interface ContextEnv {
  output?: string | null;
  deviceKind?: EventContext['deviceKind'];
  network?: EventContext['network'];
  charging?: boolean;
  batteryPct?: number;
  screenOn?: boolean;
  sessionId?: string;
  now?: Date;
}

export function buildContext(
  enabled: Partial<Record<SignalKey, boolean>>,
  details: { seeks: { fromMs: number; toMs: number }[]; volumeChanges: { atMs: number; volume: number }[]; repeatOfPrevious: boolean; sessionPosition: number; meta: PlayMeta },
  env: ContextEnv,
): EventContext | undefined {
  const c: EventContext = {};
  if (enabled.listening) {
    if (details.seeks.length) c.seeks = details.seeks;
    if (details.volumeChanges.length) c.volumeChanges = details.volumeChanges;
    c.repeatOfPrevious = details.repeatOfPrevious;
    if (details.meta.addedFrom) c.addedFrom = details.meta.addedFrom;
    if (env.sessionId) c.sessionId = env.sessionId;
    c.sessionPosition = details.sessionPosition;
  }
  if (enabled.output && env.output) c.output = env.output;
  if (enabled.device) {
    if (env.deviceKind) c.deviceKind = env.deviceKind;
    if (env.network) c.network = env.network;
    if (env.charging !== undefined) c.charging = env.charging;
    if (env.batteryPct !== undefined) c.batteryPct = env.batteryPct;
    if (env.screenOn !== undefined) c.screenOn = env.screenOn;
  }
  if (enabled.search && details.meta.searchQuery) c.searchQuery = details.meta.searchQuery;
  if (enabled.time) {
    const d = env.now ?? new Date();
    c.localHour = d.getHours();
    c.weekday = ((d.getDay() + 6) % 7) + 1; // ISO: Monday = 1
  }
  return Object.keys(c).length ? c : undefined;
}
