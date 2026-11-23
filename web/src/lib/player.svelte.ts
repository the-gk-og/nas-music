// The player: two <audio> decks (gapless preload / crossfade) → per-deck gain (levelling, fades) →
// sleep-timer gain → Web Audio EQ graph, queues (local copy mirrored to the server), Connect
// (active device vs remote), listening events, Media Session, A-B repeat, speed and pitch.
import { ApiError } from './api/client';
import { mockBackend } from './api/mock';
import { ConnectClient, extrapolatePosition, type ConnectStatus } from './connect';
import { preferServer, resumeQueueId, resumeSpot } from './resume';
import {
  PITCH_WORKLET_URL,
  buildEqGraph,
  defaultEq,
  loadProfiles,
  saveProfiles,
  semitonesToRatio,
  type EqGraph,
  type EqSettings,
} from './eq';
import { EventQueue, PlaybackTracker, WEB_SIGNALS, buildContext, signalSetting, uuid, type PlayMeta, type SignalKey } from './events';
import { APP_VERSION, defaultDeviceName, deviceKind, loadJson, preferredFormat, saveJson, storage } from './platform';
import {
  insertPosition,
  insertTracks,
  moveTrack,
  nextIndex,
  prevIndex,
  removeTrack,
  shuffleOrder,
  sortQueue,
  type QueueModel,
  type SortKey,
} from './queue';
import { family } from './family.svelte';
import { notifications } from './notifications.svelte';
import { library } from './library.svelte';
import { session } from './session.svelte';
import type { CommandAction, ConnectDevice, PlaybackState, Queue, RadioSeed, RadioStation, RepeatMode, SteerAction, Track } from './types';
import { appendNew, needsTopUp, radioContext, replaceUpcoming } from './v3';
import {
  bassIn,
  bassOut,
  correctedRate,
  expectedIn,
  planTransition,
  progress,
  rampRate,
  volIn,
  volOut,
  RAMP_BEATS,
  type DjPlan,
} from './automix';
import {
  PRELOAD_AHEAD_S,
  crossfadeFor,
  djFallbackS,
  djFor,
  defaultListening,
  fadeIn,
  fadeOut,
  levellingGain,
  loadListening,
  normaliseListening,
  saveListening,
  sleepFadeLevel,
  sleepStopsAt,
  useAlbumGain,
  type ListeningSettings,
  type SleepTimer,
} from './listening';
import { ui } from './ui.svelte';

const LOCAL_KEY = 'nm.player';
const SESSION_GAP_MS = 30 * 60 * 1000;
/** Start the next deck this long before the current one ends (covers play() latency). */
const GAPLESS_LEAD_MS = 15;
/** Fade length when the sleep timer stops at the end of a track/album. */
const SLEEP_TRACK_FADE_S = 6;

/**
 * Podcasts / audiobooks / internet radio (spokenPlayer.svelte.ts) plug into the player's Connect identity through
 * this, so they work like music across devices: reported in PlaybackState.media, controlled by remotes,
 * transferred, resumed. The music queue is untouched while a spoken item plays.
 */
export interface SpokenBridge {
  /** A spoken item is loaded here (playing or paused). */
  active(): boolean;
  playing(): boolean;
  media(): { kind: 'episode' | 'book' | 'station'; id: string } | null;
  /** Book-wide position, seconds. */
  position(): number;
  speed(): number;
  /** Load and (maybe) play an item at book-wide `positionS` (undefined = its saved resume point). */
  load(kind: 'episode' | 'book' | 'station', id: string, positionS: number | undefined, play: boolean): Promise<void>;
  play(): void;
  pause(): void;
  seek(positionS: number): void;
  setSpeed(s: number): void;
  /** Unload (music takes over). */
  close(): void;
  /** savedAt (ms) of the spoken item this browser had loaded, to compare with the music queue's. */
  savedAt(): number | null;
  restore(): Promise<void>;
}

interface LocalPrefs {
  queueId: string | null;
  index: number;
  positionS: number;
  /** song at `index` when saved, and when (resume where you left off) */
  trackId?: string | null;
  savedAt?: number;
  volume: number;
  repeat: RepeatMode;
  shuffle: boolean;
  speed: number;
  pitch: number;
  outputKey: string;
  sinkId: string;
}

export class Player {
  // ---- queues ----
  queues = $state<Queue[]>([]);
  queue = $state<QueueModel | null>(null);
  /** Queue shown on the Queues tab (may differ from the playing one). */
  viewQueue = $state<QueueModel | null>(null);
  queueMeta = new Map<string, PlayMeta>();

  // ---- local playback ----
  playing = $state(false);
  position = $state(0);
  duration = $state(0);
  buffering = $state(false);
  error = $state<string | null>(null);
  volume = $state(1);
  muted = $state(false);
  shuffle = $state(false);
  repeat = $state<RepeatMode>('off');
  speed = $state(1);
  pitch = $state(0); // semitones
  pitchAvailable = $state(false);
  ab = $state<{ a: number | null; b: number | null }>({ a: null, b: null });

  // ---- Connect ----
  connectStatus = $state<ConnectStatus>('idle');
  devices = $state<ConnectDevice[]>([]);
  remoteState = $state<PlaybackState | null>(null);
  remoteTrack = $state<Track | null>(null);
  myDeviceId = $state<string | null>(null);
  private remoteNow = $state(Date.now());
  /** Ticks every 500 ms (for extrapolating a remote device's position). */
  get now(): number {
    return this.remoteNow;
  }

  // ---- EQ / output ----
  eq = $state<EqSettings>(defaultEq());
  outputKey = $state('Default output');
  outputs = $state<{ deviceId: string; label: string }[]>([]);
  sinkId = $state('');
  sinkSupported = $state(false);

  // ---- listening: crossfade, levelling, sleep timer (per device) ----
  listening = $state<ListeningSettings>(defaultListening());
  sleep = $state<SleepTimer | null>(null);
  /** Ticks while a sleep timer runs, so labels refresh. */
  sleepNow = $state(Date.now());

  // ---- data settings (§10) ----
  signals = $state<Partial<Record<SignalKey, boolean>>>({});

  /** The deck playing the current track. */
  private audio: HTMLAudioElement | null = null;
  private decks: HTMLAudioElement[] = [];
  private deckGains: GainNode[] = [];
  private mix: GainNode | null = null;
  /** The other deck, loaded with the next track (cued at `startS`, the DJ mix-in point, or 0). */
  private preloaded: { id: string; el: HTMLAudioElement; startS: number } | null = null;
  /** Per-deck low shelf (DJ bass swap). */
  private deckBass: BiquadFilterNode[] = [];
  /** A running DJ transition: mixing both decks, then easing the incoming tempo back to 1. */
  private dj: { plan: DjPlan; out: HTMLAudioElement; inn: HTMLAudioElement; phase: 'mix' | 'ramp'; rampFrom: number } | null = null;
  private djPlans = new Map<string, DjPlan | null>();
  private preloading: string | null = null;
  private preloadFailed = new Set<string>();
  private transitioning = false;
  private fadingOut: { el: HTMLAudioElement; timer: ReturnType<typeof setTimeout> } | null = null;
  private ctx: AudioContext | null = null;
  private graph: EqGraph | null = null;
  private pitchNode: AudioWorkletNode | null = null;
  private profiles: Record<string, EqSettings> = {};
  private connect: ConnectClient | null = null;
  private tracker: PlaybackTracker;
  private events: EventQueue;
  private stateTimer: ReturnType<typeof setInterval> | null = null;
  private claimedAt = 0;
  private lastRemoteQueue: string | null = null;
  private lastQueueSave = 0;
  private loadToken = 0;
  private blobTried = new Set<string>();
  private trackCache = new Map<string, Track>();
  private sessionId = uuid();
  private lastActivity = 0;
  private battery: { charging: boolean; level: number } | null = null;
  private started = false;
  private lastLocalSave = 0;
  private adoptChecked = false;
  /** Set by spokenPlayer.svelte.ts. */
  spoken: SpokenBridge | null = null;

  constructor() {
    this.tracker = new PlaybackTracker((e, details) => {
      const now = Date.now();
      if (now - this.lastActivity > SESSION_GAP_MS) this.sessionId = uuid();
      this.lastActivity = now;
      const ctx = buildContext(this.signals, details, {
        output: this.outputKey === 'Default output' ? null : this.outputKey,
        deviceKind: deviceKind(),
        network: this.network(),
        charging: this.battery?.charging,
        batteryPct: this.battery ? Math.round(this.battery.level * 100) : undefined,
        screenOn: typeof document !== 'undefined' ? document.visibilityState === 'visible' : undefined,
        sessionId: this.sessionId,
      });
      this.events.push(ctx ? { ...e, context: ctx } : e);
    });
    this.events = new EventQueue((evs) => session.api.sendEvents(evs));
  }

  // ================= derived =================

  get current(): Track | null {
    const q = this.queue;
    return q && q.tracks.length ? (q.tracks[q.index] ?? null) : null;
  }

  /** Another device is active: this client is a remote. */
  get isRemote(): boolean {
    const id = this.remoteState?.activeDeviceId;
    return !!id && !!this.myDeviceId && id !== this.myDeviceId && this.connectStatus === 'open';
  }

  /** "Playing on Pixel", or "Casting from Pixel to Bedroom display" when that phone casts with Google Cast. */
  get remoteLabel(): string {
    const d = this.remoteDevice;
    if (!d) return '';
    if (d.kind === 'speaker') return this.castConnecting ? `Connecting to ${d.name}…` : `Playing on ${d.name}`;
    const to = this.remoteState?.castTo;
    return to ? `Casting from ${d.name} to ${to}` : `Playing on ${d.name}`;
  }

  /** The server is playing on a speaker for us (picked from the device list). */
  get castSpeaker(): ConnectDevice | null {
    const d = this.remoteDevice;
    return d?.kind === 'speaker' ? d : null;
  }

  /** That speaker is still connecting / loading. */
  get castConnecting(): boolean {
    return !!this.castSpeaker && !!this.remoteState?.connecting;
  }

  get remoteDevice(): ConnectDevice | null {
    if (!this.isRemote) return null;
    return this.devices.find((d) => d.id === this.remoteState!.activeDeviceId) ?? null;
  }

  /** What Now Playing shows. */
  get shown(): { track: Track | null; playing: boolean; position: number; duration: number; shuffle: boolean; repeat: RepeatMode; volume: number } {
    if (this.isRemote && this.remoteState) {
      const s = this.remoteState;
      const pos = extrapolatePosition(s, this.remoteNow) / 1000;
      const dur = this.remoteTrack?.durationS ?? 0;
      return { track: this.remoteTrack, playing: s.playing, position: dur ? Math.min(pos, dur) : pos, duration: dur, shuffle: s.shuffle, repeat: s.repeat, volume: s.volume };
    }
    return {
      track: this.current,
      playing: this.playing,
      position: this.position,
      duration: this.duration || this.current?.durationS || 0,
      shuffle: this.shuffle,
      repeat: this.repeat,
      volume: this.volume,
    };
  }

  // ================= startup =================

  async init() {
    if (this.started) return;
    this.started = true;
    const prefs = loadJson<LocalPrefs>(LOCAL_KEY, {
      queueId: null,
      index: 0,
      positionS: 0,
      volume: 1,
      repeat: 'off',
      shuffle: false,
      speed: 1,
      pitch: 0,
      outputKey: 'Default output',
      sinkId: '',
    });
    this.volume = prefs.volume;
    this.repeat = prefs.repeat;
    this.shuffle = prefs.shuffle;
    this.speed = prefs.speed;
    this.pitch = prefs.pitch;
    this.outputKey = prefs.outputKey;
    this.sinkId = prefs.sinkId;
    this.profiles = loadProfiles(storage);
    this.listening = loadListening(storage);
    this.eq = this.profiles[this.outputKey] ?? defaultEq();

    this.setupAudio();
    this.setupMediaSession();
    this.events.startTimer();
    void this.events.flush();
    this.watchEnvironment();
    void this.loadSignals();
    void this.refreshOutputs();

    await this.refreshQueues();
    // Resume exactly where you left off: the same queue, song and position (paused), never the top of the list.
    const spokenNewer = (this.spoken?.savedAt() ?? 0) > (prefs.savedAt ?? 0);
    const qid = resumeQueueId(this.queues, prefs);
    if (qid) {
      const q = await this.fetchQueue(qid);
      if (q) {
        const spot = resumeSpot(q, prefs);
        this.queue = this.shuffle ? { ...q, index: spot.index, order: shuffleOrder(q.tracks.length, spot.index) } : { ...q, index: spot.index };
        this.viewQueue = this.queue;
        if (this.current && !spokenNewer) this.loadCurrent(false, spot.positionS);
      }
    }
    if (spokenNewer) await this.spoken?.restore();
    this.startConnect();
    if (typeof window !== 'undefined') {
      // Closing the tab / the desktop app: keep the exact spot (local + server).
      window.addEventListener('pagehide', () => {
        this.saveLocal();
        void this.saveQueuePosition(true);
      });
    }
  }

  private setupAudio() {
    this.decks = [this.makeDeck(), this.makeDeck()];
    this.audio = this.decks[0];
    setInterval(() => this.watchTransition(), 100);
  }

  private makeDeck(): HTMLAudioElement {
    const a = new Audio();
    a.preload = 'auto';
    a.volume = 1;
    // Events from the other deck (preloading, or fading out after a crossfade) don't drive the UI.
    const mine = () => a === this.audio;
    a.addEventListener('play', () => {
      if (!mine()) return;
      this.playing = true;
      this.updateMediaSession();
      this.stateChanged();
    });
    a.addEventListener('pause', () => {
      if (!mine()) return;
      if (this.dj?.phase === 'mix') this.cancelDj();
      this.playing = false;
      this.updateMediaSession();
      this.stateChanged();
      this.saveLocal();
      void this.saveQueuePosition(true);
    });
    a.addEventListener('waiting', () => mine() && (this.buffering = true));
    a.addEventListener('playing', () => mine() && (this.buffering = false));
    a.addEventListener('loadedmetadata', () => {
      if (mine() && isFinite(a.duration)) this.duration = a.duration;
    });
    a.addEventListener('timeupdate', () => {
      if (!mine()) return;
      this.position = a.currentTime;
      this.tracker.progress(a.currentTime, this.duration || undefined);
      // A-B repeat
      const { a: A, b: B } = this.ab;
      if (A != null && B != null && a.currentTime >= B) a.currentTime = A;
      if (this.playing && Date.now() - this.lastQueueSave > 15000) void this.saveQueuePosition(false);
      if (this.playing && Date.now() - this.lastLocalSave > 5000) this.saveLocal();
    });
    a.addEventListener('ended', () => {
      if (!mine()) return;
      this.tracker.ended();
      const q = this.queue;
      const n = q ? nextIndex(q, this.repeat, false) : null;
      if (this.sleep && sleepStopsAt(this.sleep, this.current, n == null || !q ? null : q.tracks[n])) {
        this.finishSleep();
        return;
      }
      void this.advance(false);
    });
    a.addEventListener('error', () => {
      if (mine()) void this.onAudioError();
      else if (this.preloaded?.el === a) {
        this.preloadFailed.add(this.preloaded.id);
        this.preloaded = null;
      }
    });
    return a;
  }

  private otherDeck(): HTMLAudioElement | null {
    return this.decks.find((d) => d !== this.audio) ?? null;
  }

  // ================= gapless / crossfade =================

  /** 10 Hz while playing: preload the next track, start it on time, run the sleep timer. */
  private watchTransition() {
    this.watchSleep();
    if (this.dj) {
      this.djTick();
      if (this.dj?.phase === 'mix') return; // nothing else to start while two decks are mixing
    }
    const a = this.audio;
    const q = this.queue;
    const cur = this.current;
    if (!a || !q || !cur || a.paused || this.isRemote || this.transitioning) return;
    if (this.ab.a != null && this.ab.b != null) return;
    const dur = isFinite(a.duration) && a.duration > 0 ? a.duration : cur.durationS;
    if (!dur) return;
    const remaining = (dur - a.currentTime) / (a.playbackRate || 1);
    const n = nextIndex(q, this.repeat, false);
    const next = n == null ? null : q.tracks[n];
    if (this.sleep && sleepStopsAt(this.sleep, cur, next)) {
      this.setMix(sleepFadeLevel(remaining, SLEEP_TRACK_FADE_S));
      return;
    }
    if (n == null || !next || n === q.index) return; // end of queue / repeat one: the plain 'ended' path
    if (djFor(this.listening, cur, next) && !this.speedFaded()) {
      const plan = this.djPlan(cur, next);
      if (plan) {
        const toStart = (plan.outStartS - a.currentTime) / (a.playbackRate || 1);
        if (toStart <= PRELOAD_AHEAD_S) this.preload(next, plan.inStartS);
        if (toStart > 0.05) return;
        if (toStart > -0.5 && this.isPreloaded(next.id)) {
          void this.djTransition(n, plan);
          return;
        }
        // missed the start (buffering, a seek past it): the plain crossfade at the end below
      }
    }
    if (remaining <= PRELOAD_AHEAD_S) this.preload(next);
    if (!this.isPreloaded(next.id)) return;
    const fade = crossfadeFor(this.listening, cur, next);
    if (fade > 0) {
      if (remaining <= fade) void this.transition(n, fade);
    } else if (remaining <= 0.25) {
      this.transitioning = true;
      setTimeout(() => void this.transition(n, 0), Math.max(0, remaining * 1000 - GAPLESS_LEAD_MS));
    }
  }

  private isPreloaded(id: string): boolean {
    return this.preloaded?.id === id && this.preloaded.el.readyState >= 3;
  }

  private preload(t: Track, startS = 0) {
    if (this.preloaded?.id === t.id || this.preloading === t.id || this.preloadFailed.has(t.id)) return;
    const el = this.otherDeck();
    if (!el || this.fadingOut?.el === el) return; // still fading out; try again on the next tick
    this.preloading = t.id;
    this.preloaded = null;
    session.api
      .streamSrc(t.id, preferredFormat())
      .then((src) => {
        if (this.preloading !== t.id || el === this.audio) return;
        this.preloading = null;
        el.pause();
        el.crossOrigin = session.api.mode === 'bearer' && !src.startsWith('blob:') && !src.startsWith('data:') ? 'anonymous' : null;
        el.src = src;
        el.playbackRate = this.speed;
        el.preservesPitch = true;
        el.load();
        if (startS > 0) {
          const cue = () => {
            el.currentTime = startS;
            el.removeEventListener('loadedmetadata', cue);
          };
          el.addEventListener('loadedmetadata', cue);
        }
        this.preloaded = { id: t.id, el, startS };
      })
      .catch(() => {
        this.preloading = null;
        this.preloadFailed.add(t.id);
      });
  }

  /** Automatic move to queue index `n` on the preloaded deck (fadeS = 0: gapless). */
  private async transition(n: number, fadeS: number) {
    const q = this.queue;
    this.transitioning = true;
    try {
      if (!q || !this.preloaded || q.tracks[n]?.id !== this.preloaded.id) return;
      if (this.audio?.paused && !this.audio.ended) return; // paused meanwhile
      if (this.preloaded.startS) this.preloaded.el.currentTime = 0; // cued for a DJ mix that didn't happen
      this.tracker.ended();
      this.queue = { ...q, index: n };
      if (this.viewQueue?.id === q.id) this.viewQueue = this.queue;
      this.loadCurrent(true, 0, fadeS);
    } finally {
      this.transitioning = false;
    }
  }

  /** Stop a deck that is no longer current: fade it out, let it finish its last few ms, or pause now. */
  private retire(el: HTMLAudioElement, fadeS: number) {
    this.stopFading();
    if (fadeS > 0 && !el.paused) {
      this.rampDeck(el, fadeS, false);
      this.fadingOut = { el, timer: setTimeout(() => this.stopFading(), fadeS * 1000 + 100) };
    } else if (!el.paused && isFinite(el.duration) && el.duration - el.currentTime < 0.3) {
      /* gapless hand-over: it ends by itself within a few ms */
    } else el.pause();
  }

  private stopFading() {
    if (this.dj) this.cancelDj();
    if (!this.fadingOut) return;
    clearTimeout(this.fadingOut.timer);
    if (this.fadingOut.el !== this.audio) this.fadingOut.el.pause();
    this.fadingOut = null;
  }

  // ================= DJ automix =================

  /** The sleep timer is running: keep transitions plain (it may fade or stop at the end of the track). */
  private speedFaded(): boolean {
    return !!this.sleep;
  }

  private djPlan(a: Track, b: Track): DjPlan | null {
    // keyed on the analysis too: a queue refreshed from the server may bring it later
    const key = `${a.id}>${b.id}:${a.mix?.mixOutS}:${a.mix?.bpm}:${b.mix?.mixInS}:${b.mix?.bpm}:${this.listening.djBeats}:${this.listening.allowTempo}:${this.listening.crossfadeS}`;
    if (!this.djPlans.has(key)) {
      if (this.djPlans.size > 50) this.djPlans.clear();
      this.djPlans.set(key, planTransition(a, b, { beats: this.listening.djBeats, allowTempo: this.listening.allowTempo, fallbackS: djFallbackS(this.listening) }));
    }
    return this.djPlans.get(key) ?? null;
  }

  /** Start a DJ transition into queue index `n` on the preloaded (cued) deck. */
  private async djTransition(n: number, plan: DjPlan) {
    const q = this.queue;
    const pre = this.preloaded;
    const out = this.audio;
    if (!q || !pre || !out || q.tracks[n]?.id !== pre.id) return;
    if (out.paused) return;
    this.transitioning = true;
    try {
      const t = q.tracks[n];
      this.tracker.ended();
      this.queue = { ...q, index: n };
      if (this.viewQueue?.id === q.id) this.viewQueue = this.queue;
      this.preloaded = null;
      this.stopFading();
      const inn = pre.el;
      this.audio = inn;
      // land on the beat even if the outgoing deck moved on since the plan's start
      const want = expectedIn(plan, out.currentTime);
      if (Math.abs(inn.currentTime - want) > 0.01) inn.currentTime = want;
      inn.preservesPitch = true;
      inn.playbackRate = this.speed * plan.rate;
      this.dj = { plan, out, inn, phase: 'mix', rampFrom: 0 };
      this.scheduleDjCurves(plan, out, inn);
      void this.topUpRadio();
      this.tracker.start(t, this.queueMeta.get(q.id) ?? {});
      this.position = inn.currentTime;
      this.duration = isFinite(inn.duration) ? inn.duration : t.durationS;
      this.error = null;
      this.ab = { a: null, b: null };
      this.updateMediaSession();
      await this.startAudio();
      this.stateChanged();
      this.saveLocal();
      void this.saveQueuePosition(true);
    } finally {
      this.transitioning = false;
    }
  }

  /** Volume and bass curves on the audio clock, from the outgoing deck's position now. */
  private scheduleDjCurves(plan: DjPlan, out: HTMLAudioElement, inn: HTMLAudioElement) {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const x0 = progress(plan, out.currentTime);
    const left = Math.max(0.05, ((1 - x0) * plan.durationS) / (out.playbackRate || 1));
    const N = 64;
    const xs = Array.from({ length: N }, (_, i) => x0 + ((1 - x0) * i) / (N - 1));
    const gIn = this.deckGain(inn), gOut = this.deckGain(out);
    const lvlIn = this.currentGain(), lvlOut = gOut?.gain.value ?? 1;
    const set = (p: AudioParam | undefined, vals: number[]) => {
      if (!p) return;
      p.cancelScheduledValues(now);
      try {
        p.setValueCurveAtTime(Float32Array.from(vals), now, left);
      } catch {
        p.value = vals[vals.length - 1];
      }
    };
    set(gIn?.gain, xs.map((x) => lvlIn * volIn(plan, x)));
    set(gOut?.gain, xs.map((x) => lvlOut * volOut(plan, x)));
    set(this.deckBassOf(inn)?.gain, xs.map((x) => bassIn(plan, x)));
    set(this.deckBassOf(out)?.gain, xs.map((x) => bassOut(plan, x)));
  }

  /** 10 Hz during a DJ transition: keep the beats locked, finish the mix, ease the tempo back. */
  private djTick() {
    const d = this.dj;
    if (!d) return;
    if (d.inn !== this.audio) return this.cancelDj();
    if (d.phase === 'mix') {
      const x = progress(d.plan, d.out.currentTime);
      if (x >= 1 || d.out.paused || d.out.ended) {
        d.out.pause();
        this.resetDeckDsp(d.out, 0);
        d.phase = 'ramp';
        d.rampFrom = d.inn.currentTime;
        return;
      }
      if (d.plan.beatmatch && !d.inn.paused && !d.inn.seeking) {
        const err = d.inn.currentTime - expectedIn(d.plan, d.out.currentTime);
        const r = correctedRate(d.plan.rate, err);
        if (r == null) d.inn.currentTime = expectedIn(d.plan, d.out.currentTime) + 0.02;
        else d.inn.playbackRate = this.speed * r;
      }
      return;
    }
    const after = d.inn.currentTime - d.rampFrom;
    d.inn.playbackRate = this.speed * rampRate(d.plan, after);
    if (after >= RAMP_BEATS * d.plan.inBeatS || d.plan.rate === 1) {
      d.inn.playbackRate = this.speed;
      this.dj = null;
    }
  }

  /** Abort a DJ transition (manual skip, seek, pause, speed change): the current deck plays on normally. */
  private cancelDj() {
    const d = this.dj;
    if (!d) return;
    this.dj = null;
    if (d.out !== this.audio) {
      d.out.pause();
      this.resetDeckDsp(d.out, 0);
    }
    if (this.audio) {
      this.audio.playbackRate = this.speed;
      this.resetDeckDsp(this.audio, null);
    }
    this.applyDeckGain();
  }

  /** Bass back to flat; gain to `gain` (null: leave it to applyDeckGain). */
  private resetDeckDsp(el: HTMLAudioElement, gain: number | null) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    const b = this.deckBassOf(el);
    if (b) {
      b.gain.cancelScheduledValues(t);
      b.gain.setTargetAtTime(0, t, 0.02);
    }
    const g = this.deckGain(el);
    if (g && gain != null) {
      g.gain.cancelScheduledValues(t);
      g.gain.setTargetAtTime(gain, t, 0.02);
    }
  }

  private deckBassOf(el: HTMLAudioElement | null): BiquadFilterNode | null {
    return el ? (this.deckBass[this.decks.indexOf(el)] ?? null) : null;
  }

  private deckGain(el: HTMLAudioElement | null): GainNode | null {
    return el ? (this.deckGains[this.decks.indexOf(el)] ?? null) : null;
  }

  /** Levelling gain for the current track. */
  private currentGain(): number {
    return levellingGain(this.current, this.listening, useAlbumGain(this.listening, this.queue, this.queue?.index ?? 0));
  }

  /** Set the current deck's gain; `fadeInS` > 0 ramps it up from silence (crossfade). */
  private applyDeckGain(fadeInS = 0) {
    const g = this.deckGain(this.audio);
    if (!g || !this.ctx) return;
    if (fadeInS > 0) this.rampDeck(this.audio!, fadeInS, true);
    else {
      g.gain.cancelScheduledValues(this.ctx.currentTime);
      g.gain.setTargetAtTime(this.currentGain(), this.ctx.currentTime, 0.02);
    }
  }

  private rampDeck(el: HTMLAudioElement, seconds: number, up: boolean) {
    const g = this.deckGain(el);
    if (!g || !this.ctx) return;
    const from = up ? 0 : g.gain.value;
    const to = up ? this.currentGain() : 0;
    const curve = new Float32Array(32).map((_, i) => {
      const x = i / 31;
      return up ? to * fadeIn(x) : from * fadeOut(x);
    });
    const t = this.ctx.currentTime;
    g.gain.cancelScheduledValues(t);
    try {
      g.gain.setValueCurveAtTime(curve, t, seconds);
    } catch {
      g.gain.value = to;
    }
  }

  // ================= listening settings / sleep timer =================

  setListening(patch: Partial<ListeningSettings>) {
    this.listening = normaliseListening({ ...this.listening, ...patch });
    saveListening(storage, this.listening);
    this.applyDeckGain();
  }

  /** Start (or with null, cancel) the sleep timer. */
  setSleep(t: SleepTimer | null) {
    if (t?.mode === 'album') t = { ...t, albumId: this.current?.albumId };
    this.sleep = t;
    this.sleepNow = Date.now();
    this.setMix(1);
  }

  private watchSleep() {
    const s = this.sleep;
    if (!s) return;
    this.sleepNow = Date.now();
    if (s.mode !== 'minutes' || !s.endsAt) return;
    const remaining = (s.endsAt - Date.now()) / 1000;
    if (remaining <= 0) this.finishSleep();
    else if (this.playing) this.setMix(sleepFadeLevel(remaining));
  }

  private finishSleep() {
    const wasPlaying = this.playing;
    this.sleep = null;
    this.audio?.pause();
    this.stopFading();
    // Restore the level once the pause has taken effect, so the next play starts at full volume.
    setTimeout(() => this.setMix(1), 400);
    if (wasPlaying) ui.toast('Sleep timer: playback stopped');
  }

  private setMix(level: number) {
    if (this.mix && this.ctx) this.mix.gain.setTargetAtTime(level, this.ctx.currentTime, 0.05);
  }

  private async onAudioError() {
    const t = this.current;
    if (!t || !this.audio?.src) return;
    // Bearer mode: the server may not accept ?token= on /stream; fetch with the header instead.
    if (session.api.mode === 'bearer' && !session.isMock && !this.blobTried.has(t.id)) {
      this.blobTried.add(t.id);
      try {
        const at = this.audio.currentTime;
        const wasPlaying = !this.audio.paused || this.playing;
        this.audio.crossOrigin = null;
        this.audio.src = await session.api.streamBlob(t.id, preferredFormat());
        if (at) this.audio.currentTime = at;
        if (wasPlaying) await this.audio.play();
        return;
      } catch {
        /* fall through */
      }
    }
    this.error = `Can't play "${t.title}"`;
    ui.toast(this.error);
    this.buffering = false;
  }

  // ================= Web Audio =================

  /** Created on the first user gesture (autoplay policy). */
  private ensureGraph() {
    if (this.graph || !this.audio) return;
    const AC = (globalThis as unknown as { AudioContext?: typeof AudioContext }).AudioContext;
    if (!AC) return;
    try {
      this.ctx = new AC({ latencyHint: 'playback' });
      this.graph = buildEqGraph(this.ctx);
      this.mix = this.ctx.createGain();
      this.mix.connect(this.graph.input);
      this.deckBass = [];
      this.deckGains = this.decks.map((el) => {
        const src = this.ctx!.createMediaElementSource(el);
        const bass = this.ctx!.createBiquadFilter();
        bass.type = 'lowshelf';
        bass.frequency.value = 180;
        bass.gain.value = 0;
        const g = this.ctx!.createGain();
        src.connect(bass);
        bass.connect(g);
        g.connect(this.mix!);
        this.deckBass.push(bass);
        return g;
      });
      this.deckGain(this.otherDeck())!.gain.value = 0;
      this.applyDeckGain();
      this.applyEq();
      void this.applySink();
      void this.loadPitchWorklet();
    } catch (e) {
      console.warn('Web Audio unavailable, EQ disabled', e);
      this.graph = null;
    }
  }

  private async loadPitchWorklet() {
    if (!this.ctx?.audioWorklet || !this.graph) return;
    try {
      await this.ctx.audioWorklet.addModule(new URL(PITCH_WORKLET_URL, document.baseURI).href);
      this.pitchNode = new AudioWorkletNode(this.ctx, 'nm-pitch', { outputChannelCount: [2] });
      this.graph.insert(this.pitchNode);
      this.pitchAvailable = true;
      this.applyPitch();
    } catch (e) {
      console.warn('Pitch shifter unavailable', e);
    }
  }

  applyEq() {
    const s = { ...this.eq, volume: this.muted ? 0 : this.volume };
    if (this.graph) this.graph.apply(s);
    else if (this.audio) this.audio.volume = s.volume;
  }

  setEq(patch: Partial<EqSettings>) {
    this.eq = { ...this.eq, ...patch };
    this.profiles[this.outputKey] = { ...this.eq };
    saveProfiles(storage, this.profiles);
    this.applyEq();
  }

  private applyPitch() {
    const p = this.pitchNode?.parameters.get('ratio');
    if (p) p.value = semitonesToRatio(this.pitch);
  }

  // ================= outputs (EQ profile per output device) =================

  async refreshOutputs() {
    const md = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined;
    this.sinkSupported =
      typeof AudioContext !== 'undefined' && ('setSinkId' in AudioContext.prototype || 'setSinkId' in HTMLMediaElement.prototype);
    if (!md?.enumerateDevices) return;
    try {
      const list = await md.enumerateDevices();
      this.outputs = list.filter((d) => d.kind === 'audiooutput' && d.label).map((d) => ({ deviceId: d.deviceId, label: d.label }));
    } catch {
      this.outputs = [];
    }
    if (!this.outputs.length) return;
    // Follow the system default output's name as the profile key.
    const def = this.outputs.find((o) => o.deviceId === (this.sinkId || 'default'));
    if (def) this.useOutputProfile(def.label.replace(/^Default - /, ''));
  }

  /** Ask the browser for output device names (needs a permission prompt in most browsers). */
  async revealOutputs() {
    const md = navigator.mediaDevices as MediaDevices & { selectAudioOutput?: () => Promise<MediaDeviceInfo> };
    try {
      if (md.selectAudioOutput) {
        const d = await md.selectAudioOutput();
        await this.setSink(d.deviceId, d.label);
      } else {
        const s = await md.getUserMedia({ audio: true });
        s.getTracks().forEach((t) => t.stop());
      }
    } catch {
      ui.toast('The browser did not share output device names');
    }
    await this.refreshOutputs();
  }

  async setSink(deviceId: string, label: string) {
    this.sinkId = deviceId;
    await this.applySink();
    this.useOutputProfile(label.replace(/^Default - /, '') || 'Default output');
  }

  private async applySink() {
    try {
      const ctx = this.ctx as (AudioContext & { setSinkId?: (id: string) => Promise<void> }) | null;
      if (ctx?.setSinkId) await ctx.setSinkId(this.sinkId === 'default' ? '' : this.sinkId);
      else {
        for (const d of this.decks) await (d as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }).setSinkId?.(this.sinkId);
      }
    } catch {
      /* unsupported */
    }
  }

  useOutputProfile(key: string) {
    if (key === this.outputKey) return;
    this.outputKey = key;
    this.eq = this.profiles[key] ?? { ...defaultEq() };
    this.applyEq();
    this.saveLocal();
  }

  // ================= queue loading =================

  async refreshQueues() {
    try {
      this.queues = (await session.api.queues()).sort((a, b) => a.slot - b.slot);
    } catch {
      /* offline / v1 server */
    }
  }

  async fetchQueue(id: string): Promise<QueueModel | null> {
    try {
      const q = await session.api.queue(id);
      const tracks = q.tracks ?? [];
      for (const t of tracks) this.trackCache.set(t.id, t);
      return {
        id: q.id, name: q.name, slot: q.slot, tracks, index: Math.min(q.currentIndex, Math.max(0, tracks.length - 1)), radio: !!q.radio,
        positionMs: q.positionMs, updatedAt: q.updatedAt,
      };
    } catch {
      return null;
    }
  }

  async showQueue(id: string) {
    if (this.queue?.id === id) {
      this.viewQueue = this.queue;
      return;
    }
    this.viewQueue = await this.fetchQueue(id);
  }

  /**
   * Musicolet behaviour: tapping a song plays that list as a new queue. As a remote, the active device
   * is asked to do it instead.
   */
  async playList(tracks: Track[], index: number, name: string, meta: PlayMeta = {}) {
    if (!tracks.length) return;
    for (const t of tracks) this.trackCache.set(t.id, t);
    const ids = tracks.map((t) => t.id);
    if (this.isRemote) {
      this.connect?.command('playTracks', { trackIds: ids, index, queueName: name });
      return;
    }
    this.spoken?.close();
    // If the same list is already the queue, just jump.
    if (this.queue && this.queue.tracks.length === tracks.length && this.queue.tracks.every((t, i) => t.id === ids[i])) {
      this.queueMeta.set(this.queue.id, meta);
      await this.playIndex(index);
      return;
    }
    if (this.wantsAutomixOrder(meta) && tracks.length - index > 2) {
      tracks = await this.automixOrder(tracks, index);
      ids.splice(0, ids.length, ...tracks.map((t) => t.id));
    }
    let q: QueueModel;
    try {
      const created = await session.api.createQueue(name, ids, index);
      q = { id: created.id, name: created.name, slot: created.slot, tracks: tracks.slice(), index };
      void this.refreshQueues();
    } catch {
      q = { id: 'local-' + uuid(), name, slot: 0, tracks: tracks.slice(), index };
    }
    this.queueMeta.set(q.id, meta);
    this.setQueue(q);
    await this.playIndex(index);
  }

  private djOrdering(): boolean {
    return this.listening.transition === 'dj' && this.listening.automixOrder;
  }

  /** DJ automix "reorder for flow": mixes, blends, shelves and playlists (not albums, artists or search). */
  private wantsAutomixOrder(meta: PlayMeta): boolean {
    const s = this.listening;
    return s.transition === 'dj' && s.automixOrder && !this.shuffle && (meta.source === 'mix' || meta.source === 'more_like' || meta.addedFrom === 'playlist');
  }

  /** Reorder tracks after `index` for DJ flow (server: tempo, key, energy arc; nobody moves far). Never fails. */
  private async automixOrder(tracks: Track[], index: number): Promise<Track[]> {
    const head = tracks.slice(0, index);
    const rest = tracks.slice(index);
    try {
      const res = await Promise.race([
        session.api.automixOrder(rest.map((t) => t.id)),
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error('timeout')), 2000)),
      ]);
      const byId = new Map(rest.map((t) => [t.id, t]));
      const out = res.trackIds.map((id) => byId.get(id)).filter((t): t is Track => !!t);
      return out.length === rest.length ? [...head, ...out] : tracks;
    } catch {
      return tracks;
    }
  }

  private setQueue(q: QueueModel) {
    this.queue = this.shuffle ? { ...q, order: shuffleOrder(q.tracks.length, q.index) } : q;
    // The Queues tab follows the queue that starts playing.
    this.viewQueue = this.queue;
    this.ab = { a: null, b: null };
  }

  /**
   * Switch to a saved queue. With `index`: that song from the top. Without: resume where the queue was left —
   * its saved song AND position (as a remote, the server fills in that spot for the active device).
   */
  async playQueue(id: string, index?: number, startS?: number) {
    if (this.isRemote) {
      this.connect?.command('playQueue', index == null ? { queueId: id } : { queueId: id, index });
      return;
    }
    this.spoken?.close();
    if (index == null && id === this.queue?.id && this.current) {
      // Already loaded here: just carry on from where it is.
      this.claim();
      if (startS != null) this.seek(startS);
      await this.play();
      return;
    }
    const q = id === this.queue?.id ? this.queue : await this.fetchQueue(id);
    if (!q) return;
    const i = index ?? q.index;
    this.setQueue({ ...q, index: i });
    await this.playIndex(i, startS ?? (index == null ? (q.positionMs ?? 0) / 1000 : 0));
  }

  async playIndex(i: number, startS = 0) {
    if (!this.queue) return;
    this.queue = { ...this.queue, index: i };
    if (this.viewQueue?.id === this.queue.id) this.viewQueue = this.queue;
    this.claim();
    this.loadCurrent(true, startS);
  }

  // ================= radio (v3 §5) =================

  /** Start an endless radio queue ("<seed> radio") and play it here, or on the active device as a remote. */
  async startRadio(seed: RadioSeed, label = 'Radio') {
    ui.toast(`Starting ${label}…`);
    let created: Queue;
    try {
      created = await session.api.startRadio(seed, seed.type === 'moment' ? radioContext(new Date(), deviceKind()) : undefined, this.djOrdering());
    } catch (e) {
      ui.toast(e instanceof ApiError && (e.status === 404 || e.status === 503) ? 'Radio isn’t available on this server yet' : (e as Error).message);
      return;
    }
    const tracks = created.tracks ?? [];
    for (const t of tracks) this.trackCache.set(t.id, t);
    void this.refreshQueues();
    if (this.isRemote) {
      this.connect?.command('playQueue', { queueId: created.id, index: 0 });
      return;
    }
    this.queueMeta.set(created.id, { source: 'mix', addedFrom: 'radio' });
    this.setQueue({ id: created.id, name: created.name, slot: created.slot, tracks, index: 0, radio: true });
    await this.playIndex(0);
  }

  /** AI radio (v6): steer the radio that's playing (here or on the active device). Returns the station. */
  async steerRadio(action: SteerAction, scope: 'track' | 'artist' = 'track', refId?: string): Promise<RadioStation | null> {
    const remote = this.isRemote;
    const qid = remote ? this.remoteState?.queueId : this.queue?.id;
    const track = remote ? this.remoteTrack : this.current;
    if (!qid) return null;
    const needsTrack = action === 'more_like' || action === 'less_like' || action === 'never_here';
    if (needsTrack && !track) return null;
    try {
      const res = await session.api.steerRadio(qid, {
        action,
        scope,
        trackId: needsTrack ? track!.id : null,
        refId: refId ?? null,
        currentIndex: remote ? (this.remoteState?.index ?? null) : (this.queue?.index ?? null),
      });
      const fresh = res.queue.tracks ?? [];
      for (const t of fresh) this.trackCache.set(t.id, t);
      if (!remote && this.queue?.id === qid) {
        const tracks = replaceUpcoming(this.queue.tracks, fresh, this.queue.index);
        this.queue = { ...this.queue, tracks, order: this.shuffle ? shuffleOrder(tracks.length, this.queue.index) : null };
        if (this.viewQueue?.id === qid) this.viewQueue = this.queue;
      }
      if (action === 'never_here' && !remote && track && this.current?.id === track.id) void this.next();
      return res.station;
    } catch (e) {
      ui.toast(e instanceof ApiError && (e.status === 404 || e.status === 503) ? 'Steering isn’t available on this server yet' : (e as Error).message);
      return null;
    }
  }

  private toppingUp = false;
  private async topUpRadio() {
    const q = this.queue;
    if (!q?.radio || this.toppingUp || !needsTopUp(q.tracks.length, q.index)) return;
    this.toppingUp = true;
    try {
      const more = await session.api.radioMore(q.id, 25, this.djOrdering());
      const fresh = more.tracks ?? [];
      for (const t of fresh) this.trackCache.set(t.id, t);
      if (this.queue?.id !== q.id) return;
      const tracks = appendNew(this.queue.tracks, fresh);
      this.queue = { ...this.queue, tracks, order: this.shuffle ? shuffleOrder(tracks.length, this.queue.index) : null };
      if (this.viewQueue?.id === q.id) this.viewQueue = this.queue;
    } catch {
      /* try again on the next track */
    } finally {
      this.toppingUp = false;
    }
  }

  /** Load the current track into <audio>. Uses the preloaded deck when it holds this track
   *  (gapless; `fadeS` > 0 crossfades from the old deck). */
  private loadCurrent(autoplay: boolean, startS = 0, fadeS = 0) {
    const t = this.current;
    let a = this.audio;
    if (!t || !a) return;
    const token = ++this.loadToken;
    const pre = this.preloaded;
    if (pre && pre.id === t.id && pre.el !== a && !startS && pre.el.readyState >= 2) {
      this.preloaded = null;
      if (pre.startS) pre.el.currentTime = 0; // cued for a DJ mix: a manual jump starts from the top
      const old = a;
      this.audio = a = pre.el;
      this.retire(old, autoplay ? fadeS : 0);
      void this.topUpRadio();
      this.tracker.start(t, this.queue ? (this.queueMeta.get(this.queue.id) ?? {}) : {});
      this.position = 0;
      this.duration = isFinite(a.duration) ? a.duration : t.durationS;
      this.error = null;
      this.ab = { a: null, b: null };
      this.updateMediaSession();
      this.applyDeckGain(autoplay ? fadeS : 0);
      a.playbackRate = this.speed;
      if (autoplay) void this.startAudio();
      else this.playing = false;
      this.stateChanged();
      this.saveLocal();
      void this.saveQueuePosition(true);
      return;
    }
    // A manual jump: stop whatever the other deck is doing.
    this.stopFading();
    const other = this.otherDeck();
    if (other && !other.paused) other.pause();
    this.applyDeckGain();
    void this.topUpRadio();
    this.tracker.start(t, this.queue ? (this.queueMeta.get(this.queue.id) ?? {}) : {});
    this.position = startS;
    this.duration = t.durationS;
    this.error = null;
    this.ab = { a: null, b: null };
    this.updateMediaSession();
    void session.api.streamSrc(t.id, preferredFormat()).then(async (src) => {
      if (token !== this.loadToken) return;
      a.crossOrigin = session.api.mode === 'bearer' && !src.startsWith('blob:') && !src.startsWith('data:') ? 'anonymous' : null;
      a.src = src;
      a.playbackRate = this.speed;
      a.preservesPitch = true;
      if (startS) {
        const seek = () => {
          a.currentTime = startS;
          a.removeEventListener('loadedmetadata', seek);
        };
        a.addEventListener('loadedmetadata', seek);
      }
      if (autoplay) await this.startAudio();
      this.stateChanged();
      this.saveLocal();
      void this.saveQueuePosition(true);
    });
  }

  private async startAudio() {
    this.ensureGraph();
    if (this.ctx?.state === 'suspended') await this.ctx.resume().catch(() => {});
    try {
      await this.audio!.play();
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        this.playing = false;
        if ((e as Error).name === 'NotAllowedError') ui.toast('Press play to start audio');
      }
    }
  }

  private async advance(manual: boolean) {
    const q = this.queue;
    if (!q) return;
    const n = nextIndex(q, this.repeat, manual);
    if (n == null) {
      this.playing = false;
      this.stateChanged();
      return;
    }
    if (n === q.index && !manual) {
      this.tracker.start(q.tracks[n], this.queueMeta.get(q.id) ?? {});
      this.audio!.currentTime = 0;
      await this.startAudio();
      return;
    }
    await this.playIndex(n);
  }

  // ================= transport (local or remote) =================

  private remote(action: CommandAction, args: Record<string, unknown> = {}): boolean {
    if (!this.isRemote) return false;
    this.connect?.command(action, args);
    return true;
  }

  async play() {
    if (this.remote('play')) return;
    if (!this.current) return;
    this.claim();
    if (!this.audio?.src) {
      this.loadCurrent(true, this.position);
      return;
    }
    await this.startAudio();
  }

  pause() {
    if (this.remote('pause')) return;
    this.audio?.pause();
    this.stopFading();
  }

  toggle() {
    if (this.shown.playing) this.pause();
    else void this.play();
  }

  async next() {
    if (this.remote('next')) return;
    this.tracker.finish(this.position);
    await this.advance(true);
  }

  async prev() {
    if (this.remote('prev')) return;
    if (this.position > 3 && this.audio) {
      this.seek(0);
      return;
    }
    const q = this.queue;
    if (!q) return;
    const p = prevIndex(q, this.repeat);
    if (p == null) return;
    this.tracker.finish(this.position);
    await this.playIndex(p);
  }

  seek(sec: number) {
    if (this.remote('seek', { positionMs: Math.round(sec * 1000) })) return;
    const a = this.audio;
    if (!a) return;
    const to = Math.max(0, Math.min(sec, this.duration || sec));
    this.cancelDj();
    this.tracker.seek(a.currentTime, to);
    a.currentTime = to;
    this.position = to;
    this.stateChanged();
  }

  seekBy(d: number) {
    this.seek(this.shown.position + d);
  }

  setVolume(v: number) {
    v = Math.max(0, Math.min(1, v));
    if (this.remote('setVolume', { volume: v })) return;
    this.volume = v;
    this.muted = false;
    this.tracker.volume(this.position, v);
    this.applyEq();
    this.saveLocal();
    this.stateChanged();
  }

  toggleMute() {
    this.muted = !this.muted;
    this.applyEq();
  }

  toggleShuffle() {
    if (this.remote('setShuffle', { on: !this.shown.shuffle })) return;
    this.shuffle = !this.shuffle;
    if (this.queue) this.queue = { ...this.queue, order: this.shuffle ? shuffleOrder(this.queue.tracks.length, this.queue.index) : null };
    this.saveLocal();
    this.stateChanged();
  }

  cycleRepeat() {
    const next: RepeatMode = this.shown.repeat === 'off' ? 'all' : this.shown.repeat === 'all' ? 'one' : 'off';
    if (this.remote('setRepeat', { mode: next })) return;
    this.repeat = next;
    this.saveLocal();
    this.stateChanged();
  }

  setSpeed(s: number) {
    s = Math.max(0.25, Math.min(3, Math.round(s * 100) / 100));
    if (this.remote('setSpeed', { speed: s })) return;
    this.speed = s;
    this.cancelDj();
    for (const d of this.decks) d.playbackRate = s;
    this.saveLocal();
    this.stateChanged();
  }

  setPitch(semitones: number) {
    this.pitch = Math.max(-12, Math.min(12, Math.round(semitones * 2) / 2));
    this.applyPitch();
    this.saveLocal();
  }

  // A-B repeat: first call sets A, second sets B, third clears.
  abStep() {
    const pos = this.position;
    if (this.ab.a == null) this.ab = { a: pos, b: null };
    else if (this.ab.b == null) {
      if (pos <= this.ab.a + 0.5) {
        ui.toast('B must be after A');
        return;
      }
      this.ab = { a: this.ab.a, b: pos };
      this.seek(this.ab.a!);
    } else this.ab = { a: null, b: null };
  }

  clearAb() {
    this.ab = { a: null, b: null };
  }

  // ================= queue editing =================

  private isPlayingQueue(id: string) {
    return this.queue?.id === id;
  }

  private async mutate(id: string, local: (q: QueueModel) => QueueModel, server: () => Promise<unknown>) {
    const apply = (q: QueueModel | null) => (q && q.id === id ? local(q) : q);
    const playing = this.isPlayingQueue(id);
    const before = this.queue;
    this.queue = apply(this.queue);
    this.viewQueue = playing ? this.queue : apply(this.viewQueue);
    if (!id.startsWith('local-')) {
      try {
        await server();
      } catch (e) {
        ui.toast(`Queue not saved on the server: ${(e as Error).message}`);
      }
    }
    if (playing && before && this.current?.id !== before.tracks[before.index]?.id && this.playing) this.loadCurrent(true);
    this.stateChanged();
    void this.refreshQueues();
  }

  async moveInQueue(id: string, from: number, to: number) {
    await this.mutate(id, (q) => moveTrack(q, from, to), () => session.api.moveQueueTrack(id, from, to));
  }

  async removeFromQueue(id: string, pos: number) {
    await this.mutate(id, (q) => removeTrack(q, pos), () => session.api.removeFromQueue(id, pos));
  }

  async addToQueue(tracks: Track[], position: 'next' | 'end', queueId?: string) {
    const id = queueId ?? this.queue?.id;
    if (!id) {
      await this.playList(tracks, 0, 'Queue');
      return;
    }
    const target = id === this.queue?.id ? this.queue : id === this.viewQueue?.id ? this.viewQueue : null;
    const pos = target ? insertPosition(target, position) : position;
    await this.mutate(id, (q) => insertTracks(q, tracks, position), () => session.api.addToQueue(id, tracks.map((t) => t.id), pos));
    ui.toast(position === 'next' ? 'Will play next' : 'Added to queue');
  }

  async sortQueue(id: string, key: SortKey) {
    const q = id === this.queue?.id ? this.queue : this.viewQueue;
    if (!q) return;
    const sorted = sortQueue(q, key);
    // Server has move only: rebuild as a new order via a replacement queue is heavier; replay moves.
    const ids = q.tracks.map((t) => t.id);
    const target = sorted.tracks.map((t) => t.id);
    const moves: [number, number][] = [];
    const cur = ids.slice();
    for (let i = 0; i < target.length; i++) {
      const from = cur.indexOf(target[i], i);
      if (from !== i) {
        moves.push([from, i]);
        const [x] = cur.splice(from, 1);
        cur.splice(i, 0, x);
      }
    }
    await this.mutate(id, () => sorted, async () => {
      for (const [f, t] of moves) await session.api.moveQueueTrack(id, f, t);
    });
  }

  async clearQueue(id: string) {
    const q = id === this.queue?.id ? this.queue : this.viewQueue;
    if (!q) return;
    const n = q.tracks.length;
    await this.mutate(id, (x) => ({ ...x, tracks: [], index: 0, order: null }), async () => {
      for (let i = n - 1; i >= 0; i--) await session.api.removeFromQueue(id, i);
    });
    if (this.isPlayingQueue(id)) {
      this.audio?.pause();
      this.audio?.removeAttribute('src');
    }
  }

  async renameQueue(id: string, name: string) {
    try {
      await session.api.patchQueue(id, { name });
    } catch (e) {
      ui.toast((e as Error).message);
    }
    if (this.queue?.id === id) this.queue = { ...this.queue, name };
    if (this.viewQueue?.id === id) this.viewQueue = { ...this.viewQueue, name };
    await this.refreshQueues();
  }

  async deleteQueue(id: string) {
    try {
      await session.api.deleteQueue(id);
    } catch (e) {
      ui.toast((e as Error).message);
      return;
    }
    await this.refreshQueues();
    if (this.viewQueue?.id === id) this.viewQueue = this.queue?.id === id ? null : this.queue;
    if (!this.viewQueue && this.queues[0]) await this.showQueue(this.queues[0].id);
  }

  async saveQueueAsPlaylist(id: string, name: string) {
    try {
      await session.api.saveQueueAsPlaylist(id, name);
      ui.toast(`Saved as playlist "${name}"`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  private async saveQueuePosition(force: boolean) {
    const q = this.queue;
    if (!q || q.id.startsWith('local-') || this.isRemote) return;
    if (!force && Date.now() - this.lastQueueSave < 15000) return;
    this.lastQueueSave = Date.now();
    try {
      await session.api.patchQueue(q.id, { currentIndex: q.index, positionMs: Math.round(this.position * 1000) });
    } catch {
      /* offline */
    }
  }

  private saveLocal() {
    this.lastLocalSave = Date.now();
    saveJson(LOCAL_KEY, {
      queueId: this.queue?.id ?? null,
      index: this.queue?.index ?? 0,
      positionS: this.position,
      trackId: this.current?.id ?? null,
      savedAt: this.spoken?.active() ? (loadJson<LocalPrefs | null>(LOCAL_KEY, null)?.savedAt ?? 0) : Date.now(),
      volume: this.volume,
      repeat: this.repeat,
      shuffle: this.shuffle,
      speed: this.speed,
      pitch: this.pitch,
      outputKey: this.outputKey,
      sinkId: this.sinkId,
    } satisfies LocalPrefs);
  }

  // ================= Connect =================

  private startConnect() {
    const api = () => session.api;
    const mock = session.isMock;
    this.connect = new ConnectClient({
      url: () => api().connectUrl,
      hello: { deviceName: session.saved.deviceName || defaultDeviceName(), kind: deviceKind(), canPlay: true, appVersion: APP_VERSION },
      socketFactory: (url) => (mock ? mockBackend().socket(url) : (new WebSocket(url) as unknown as import('./connect').SocketLike)),
      handlers: {
        onStatus: (s) => (this.connectStatus = s),
        onDevices: (d) => {
          this.devices = d;
          this.myDeviceId = this.connect?.deviceId ?? null;
        },
        onState: (s) => {
          this.myDeviceId = this.connect?.deviceId ?? null;
          this.remoteState = s;
          if (!this.adoptChecked) {
            this.adoptChecked = true;
            void this.adoptIfNewer(s);
          }
          if (s.activeDeviceId && s.activeDeviceId !== this.myDeviceId) {
            void this.loadRemoteTrack(s.trackId);
            // As a remote, the Queue view follows the queue playing on the active device.
            if (s.queueId && s.queueId !== this.viewQueue?.id && s.queueId !== this.lastRemoteQueue) {
              this.lastRemoteQueue = s.queueId;
              void this.showQueue(s.queueId);
            }
          }
        },
        onTakeover: (s) => void this.takeover(s),
        onRelease: () => this.release(),
        onCommand: (action, args) => void this.execute(action, args),
        onQueueChanged: (qid) => void this.queueChangedElsewhere(qid),
        // Speakers (and the hub) report problems this way: "Kitchen: the speaker couldn't play this track".
        onError: (detail, source) => {
          if (source === 'speaker') ui.toast(detail);
        },
        onPlaylistChanged: (pid, rev, deleted) => library.playlistChanged(pid, rev, deleted),
        onFamilyChanged: (f) => family.changed(f),
        onNotification: (n) => notifications.onPush(n),
      },
    });
    this.connect.start();
    let ticks = 0;
    this.stateTimer = setInterval(() => {
      this.remoteNow = Date.now();
      // §5: the active device reports state every 5 s while playing.
      if (++ticks % 10 === 0 && (this.playing || this.spoken?.playing()) && this.connect?.isActive) this.reportState();
    }, 500);
    if (typeof window !== 'undefined') {
      window.addEventListener('online', () => this.connect?.kick());
      document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && this.connect?.kick());
      window.addEventListener('focus', () => this.connect?.kick());
    }
  }

  stopConnect() {
    this.connect?.stop();
    if (this.stateTimer) clearInterval(this.stateTimer);
  }

  private async loadRemoteTrack(id: string | null) {
    if (!id) {
      this.remoteTrack = null;
      return;
    }
    if (this.remoteTrack?.id === id) return;
    let t = this.trackCache.get(id);
    if (!t) {
      try {
        t = await session.api.track(id);
        this.trackCache.set(id, t);
      } catch {
        return;
      }
    }
    this.remoteTrack = t;
  }

  /** Starting playback here: become the active device first (§5). */
  private claim() {
    if (this.connect && this.connect.status === 'open' && !this.connect.isActive) {
      if (this.connect.claim(true)) this.claimedAt = Date.now();
      this.remoteState = this.connect.state;
    }
  }

  /**
   * Another device played something more recently than this browser (the phone, then you open the web player):
   * show that here, paused at its spot, instead of this browser's older one.
   */
  private async adoptIfNewer(s: PlaybackState) {
    if (this.isRemote || this.playing || this.spoken?.playing()) return;
    const local = Math.max(loadJson<LocalPrefs | null>(LOCAL_KEY, null)?.savedAt ?? 0, this.spoken?.savedAt() ?? 0) || null;
    if (!preferServer(local, s, this.myDeviceId)) return;
    if (s.media) {
      await this.spoken?.load(s.media.kind, s.media.id, s.positionMs / 1000, false);
      return;
    }
    if (!s.queueId) return;
    const q = await this.fetchQueue(s.queueId);
    if (!q || !q.tracks.length || this.playing) return;
    const found = s.trackId ? q.tracks.findIndex((t) => t.id === s.trackId) : -1;
    this.spoken?.close();
    this.setQueue({ ...q, index: found >= 0 ? found : Math.min(s.index, q.tracks.length - 1) });
    this.loadCurrent(false, s.positionMs / 1000);
    this.saveLocal();
  }

  /** As a remote: ask the active device to play a podcast episode / audiobook / station. */
  remotePlaySpoken(kind: 'episode' | 'book' | 'station', id: string, positionS?: number) {
    this.connect?.command('playSpoken', positionS == null ? { kind, id } : { kind, id, positionMs: Math.round(positionS * 1000) });
  }

  /** A spoken item started or stopped here: become the active device when it starts, report the state. */
  spokenChanged(started: boolean) {
    if (started) {
      if (this.playing) this.pause();
      this.claim();
    }
    this.reportState();
  }

  private buildState(): PlaybackState {
    const sp = this.spoken;
    const media = sp?.active() ? sp.media() : null;
    if (sp && media) {
      return {
        activeDeviceId: this.myDeviceId, queueId: null, index: 0, trackId: null,
        positionMs: media.kind === 'station' ? 0 : Math.round(sp.position() * 1000), positionAt: new Date().toISOString(),
        playing: sp.playing(), volume: this.volume, shuffle: false, repeat: 'off', speed: sp.speed(), media,
        rev: this.connect?.state?.rev ?? 0,
      };
    }
    const q = this.queue;
    return {
      activeDeviceId: this.myDeviceId,
      queueId: q?.id ?? null,
      index: q?.index ?? 0,
      trackId: this.current?.id ?? null,
      positionMs: Math.round(this.position * 1000),
      positionAt: new Date().toISOString(),
      playing: this.playing,
      volume: this.volume,
      shuffle: this.shuffle,
      repeat: this.repeat,
      speed: this.speed,
      rev: this.connect?.state?.rev ?? 0,
    };
  }

  private reportState() {
    if (!this.connect?.isActive) return;
    this.connect.reportState(this.buildState());
    this.remoteState = this.connect.state;
  }

  private stateChanged() {
    this.reportState();
  }

  private async takeover(s: PlaybackState) {
    this.remoteState = this.connect?.state ?? s;
    if (s.media) {
      if (this.playing) this.pause();
      const sp = this.spoken;
      const pos = extrapolatePosition(s, Date.now()) / 1000;
      const same = sp?.active() && sp.media()?.kind === s.media.kind && sp.media()?.id === s.media.id;
      if (same && Date.now() - this.claimedAt < 5000) {
        this.reportState();
        return;
      }
      if (same) {
        sp!.seek(pos);
        if (s.playing) sp!.play();
        else sp!.pause();
      } else await sp?.load(s.media.kind, s.media.id, pos, s.playing);
      ui.toast('Playing on this device');
      return;
    }
    this.spoken?.close();
    // We just claimed it ourselves and are already playing the right thing.
    if (Date.now() - this.claimedAt < 5000 && (!s.trackId || s.trackId === this.current?.id)) {
      this.reportState();
      return;
    }
    const startS = extrapolatePosition(s, Date.now()) / 1000;
    if (s.queueId) {
      const q = s.queueId === this.queue?.id ? this.queue : await this.fetchQueue(s.queueId);
      if (q) {
        const idx = s.trackId ? Math.max(0, q.tracks.findIndex((t) => t.id === s.trackId)) : s.index;
        this.setQueue({ ...q, index: q.tracks[s.index]?.id === s.trackId ? s.index : idx });
      }
    }
    this.shuffle = s.shuffle;
    this.repeat = s.repeat;
    if (s.speed) this.speed = s.speed;
    if (typeof s.volume === 'number') this.volume = s.volume;
    this.applyEq();
    if (this.current) this.loadCurrent(s.playing, startS);
    ui.toast('Playing on this device');
  }

  private release() {
    if (this.playing) this.tracker.finish(this.position);
    this.spoken?.pause();
    this.audio?.pause();
    this.stopFading();
    this.remoteState = this.connect?.state ?? this.remoteState;
  }

  /** Commands from a remote, executed here because we're the active device. */
  private async execute(action: CommandAction, args: Record<string, unknown>) {
    const sp = this.spoken;
    if (action === 'playSpoken') {
      const kind = args.kind as 'episode' | 'book' | 'station';
      if (!kind || typeof args.id !== 'string') return;
      if (this.playing) this.pause();
      return sp?.load(kind, args.id, typeof args.positionMs === 'number' ? args.positionMs / 1000 : undefined, true);
    }
    if (sp?.active()) {
      // A podcast / audiobook / station is loaded: transport commands go to it (next/prev skip ±).
      switch (action) {
        case 'play':
          return sp.play();
        case 'pause':
          return sp.pause();
        case 'seek':
          return sp.seek(Number(args.positionMs ?? 0) / 1000);
        case 'next':
          return sp.seek(sp.position() + 30);
        case 'prev':
          return sp.seek(Math.max(0, sp.position() - 15));
        case 'setSpeed':
          return sp.setSpeed(Number(args.speed));
        case 'setVolume':
          return this.setVolume(Number(args.volume));
        default:
          sp.close();
      }
    }
    switch (action) {
      case 'play':
        return this.play();
      case 'pause':
        return this.pause();
      case 'next':
        return this.next();
      case 'prev':
        return this.prev();
      case 'seek':
        return this.seek(Number(args.positionMs ?? 0) / 1000);
      case 'setVolume':
        return this.setVolume(Number(args.volume));
      case 'setShuffle':
        if (!!args.on !== this.shuffle) this.toggleShuffle();
        return;
      case 'setRepeat':
        this.repeat = (args.mode as RepeatMode) ?? 'off';
        this.stateChanged();
        return;
      case 'setSpeed':
        return this.setSpeed(Number(args.speed));
      case 'playQueue':
        // No index = resume where the queue was left (the server adds its saved index + positionMs).
        return this.playQueue(
          String(args.queueId),
          typeof args.index === 'number' ? args.index : undefined,
          typeof args.positionMs === 'number' ? args.positionMs / 1000 : undefined,
        );
      case 'playTracks': {
        const ids = (args.trackIds as string[]) ?? [];
        const tracks = (await Promise.all(ids.map((id) => this.trackById(id)))).filter((t): t is Track => !!t);
        return this.playList(tracks, Number(args.index ?? 0), String(args.queueName ?? 'Queue'));
      }
    }
  }

  private async trackById(id: string): Promise<Track | null> {
    const c = this.trackCache.get(id);
    if (c) return c;
    try {
      const t = await session.api.track(id);
      this.trackCache.set(id, t);
      return t;
    } catch {
      return null;
    }
  }

  private async queueChangedElsewhere(qid: string) {
    await this.refreshQueues();
    const fresh = qid === this.queue?.id || qid === this.viewQueue?.id ? await this.fetchQueue(qid) : null;
    if (!fresh) return;
    if (qid === this.queue?.id && this.queue) {
      // Keep our current track current.
      const curId = this.current?.id;
      const idx = curId ? fresh.tracks.findIndex((t) => t.id === curId) : -1;
      this.queue = { ...fresh, index: idx >= 0 ? idx : Math.min(this.queue.index, fresh.tracks.length - 1), order: null };
      if (this.viewQueue?.id === qid) this.viewQueue = this.queue;
    } else if (qid === this.viewQueue?.id) this.viewQueue = fresh;
  }

  /** Devices menu: move playback to another device (or here). */
  transfer(deviceId: string) {
    if (!this.connect) return;
    const here = deviceId === this.myDeviceId;
    if (!here && this.connect.isActive) {
      // Make sure the target gets an up-to-date position.
      this.reportState();
      this.tracker.finish(this.position);
    }
    const play = this.shown.playing || here;
    this.connect.transfer(deviceId, play);
    if (here) this.claimedAt = 0;
  }

  /** "Stop casting" / Cancel while connecting: the speaker stops, playback comes back here paused. */
  stopCasting() {
    if (!this.connect || !this.myDeviceId) return;
    this.connect.transfer(this.myDeviceId, false);
    this.claimedAt = 0;
  }

  // ================= Media Session + environment =================

  private setupMediaSession() {
    const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : undefined;
    if (!ms) return;
    const h = (a: MediaSessionAction, fn: MediaSessionActionHandler) => {
      try {
        ms.setActionHandler(a, fn);
      } catch {
        /* unsupported action */
      }
    };
    h('play', () => void this.play());
    h('pause', () => this.pause());
    h('stop', () => this.pause());
    h('nexttrack', () => void this.next());
    h('previoustrack', () => void this.prev());
    h('seekto', (d) => d.seekTime != null && this.seek(d.seekTime));
    h('seekbackward', (d) => this.seekBy(-(d.seekOffset ?? 10)));
    h('seekforward', (d) => this.seekBy(d.seekOffset ?? 10));
  }

  private mediaTrackId: string | null = null;
  private updateMediaSession() {
    const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : undefined;
    const t = this.current;
    if (!ms) return;
    ms.playbackState = this.playing ? 'playing' : 'paused';
    try {
      if (this.duration && isFinite(this.duration))
        ms.setPositionState({ duration: this.duration, position: Math.min(this.position, this.duration), playbackRate: this.speed });
    } catch {
      /* ignore */
    }
    if (!t || this.mediaTrackId === t.id) return;
    this.mediaTrackId = t.id;
    ms.metadata = new MediaMetadata({ title: t.title, artist: t.artist, album: t.album });
    void Promise.all([session.api.artSrc(t.albumId, 512), session.api.artSrc(t.albumId, 256)])
      .then(([big, small]) => {
        if (this.current?.id !== t.id) return;
        ms.metadata = new MediaMetadata({
          title: t.title,
          artist: t.artist,
          album: t.album,
          artwork: [
            { src: small, sizes: '256x256', type: 'image/jpeg' },
            { src: big, sizes: '512x512', type: 'image/jpeg' },
          ],
        });
      })
      .catch(() => {});
    this.onTrackChange?.(t);
  }

  /** Desktop shell hook (native notification on track change). */
  onTrackChange: ((t: Track) => void) | null = null;

  private network(): 'wifi' | 'cellular' | 'ethernet' | 'none' | undefined {
    const c = (navigator as Navigator & { connection?: { type?: string } }).connection;
    const t = c?.type;
    if (t === 'wifi' || t === 'cellular' || t === 'ethernet' || t === 'none') return t;
    return undefined;
  }

  private watchEnvironment() {
    const nav = navigator as Navigator & { getBattery?: () => Promise<{ charging: boolean; level: number; addEventListener: (e: string, f: () => void) => void }> };
    nav.getBattery?.()
      .then((b) => {
        const upd = () => (this.battery = { charging: b.charging, level: b.level });
        upd();
        b.addEventListener('chargingchange', upd);
        b.addEventListener('levelchange', upd);
      })
      .catch(() => {});
    navigator.mediaDevices?.addEventListener?.('devicechange', () => void this.refreshOutputs());
    window.addEventListener('pagehide', () => {
      this.saveLocal();
      void this.events.flush();
    });
  }

  // ================= data settings (§10) =================

  async loadSignals() {
    try {
      const s = (await session.api.getSettings()).settings ?? {};
      const out: Partial<Record<SignalKey, boolean>> = {};
      for (const sig of WEB_SIGNALS) out[sig.key] = s[signalSetting(sig.key)] === true;
      this.signals = out;
    } catch {
      this.signals = loadJson('nm.signals', {});
    }
  }

  async setSignals(patch: Partial<Record<SignalKey, boolean>>) {
    this.signals = { ...this.signals, ...patch };
    saveJson('nm.signals', this.signals);
    const body: Record<string, boolean> = {};
    for (const [k, v] of Object.entries(patch)) body[signalSetting(k as SignalKey)] = !!v;
    try {
      await session.api.putSettings({ settings: body });
    } catch (e) {
      ui.toast(e instanceof ApiError && e.status === 404 ? 'Saved on this device only (server has no settings endpoint yet)' : 'Saved on this device only');
    }
  }

  // ================= favourites =================

  async toggleStar(t: Track) {
    const on = !t.starred;
    const apply = (x: Track) => (x.id === t.id ? { ...x, starred: on } : x);
    if (this.queue) this.queue = { ...this.queue, tracks: this.queue.tracks.map(apply) };
    if (this.viewQueue) this.viewQueue = this.viewQueue.id === this.queue?.id ? this.queue : { ...this.viewQueue, tracks: this.viewQueue.tracks.map(apply) };
    if (this.remoteTrack?.id === t.id) this.remoteTrack = { ...this.remoteTrack, starred: on };
    t.starred = on;
    try {
      await (on ? session.api.star(t.id) : session.api.unstar(t.id));
    } catch (e) {
      ui.toast((e as Error).message);
    }
    this.events.push({ id: uuid(), type: on ? 'star' : 'unstar', trackId: t.id, at: new Date().toISOString() });
    ui.toast(on ? 'Added to favourites' : 'Removed from favourites');
  }

  flushEvents() {
    return this.events.flush();
  }

  /** Sign-out: stop audio and sockets, drop local state. */
  reset() {
    this.stopConnect();
    this.audio?.pause();
    this.events.stopTimer();
    void this.events.flush();
  }
}

export const player = new Player();
