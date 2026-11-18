// The player: <audio> → Web Audio EQ graph, queues (local copy mirrored to the server), Connect
// (active device vs remote), listening events, Media Session, A-B repeat, speed and pitch.
import { ApiError } from './api/client';
import { mockBackend } from './api/mock';
import { ConnectClient, extrapolatePosition, type ConnectStatus } from './connect';
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
import { session } from './session.svelte';
import type { CommandAction, ConnectDevice, PlaybackState, Queue, RepeatMode, Track } from './types';
import { ui } from './ui.svelte';

const LOCAL_KEY = 'nm.player';
const SESSION_GAP_MS = 30 * 60 * 1000;

interface LocalPrefs {
  queueId: string | null;
  index: number;
  positionS: number;
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

  // ---- EQ / output ----
  eq = $state<EqSettings>(defaultEq());
  outputKey = $state('Default output');
  outputs = $state<{ deviceId: string; label: string }[]>([]);
  sinkId = $state('');
  sinkSupported = $state(false);

  // ---- data settings (§10) ----
  signals = $state<Partial<Record<SignalKey, boolean>>>({});

  private audio: HTMLAudioElement | null = null;
  private ctx: AudioContext | null = null;
  private graph: EqGraph | null = null;
  private pitchNode: AudioWorkletNode | null = null;
  private profiles: Record<string, EqSettings> = {};
  private connect: ConnectClient | null = null;
  private tracker: PlaybackTracker;
  private events: EventQueue;
  private stateTimer: ReturnType<typeof setInterval> | null = null;
  private claimedAt = 0;
  private lastQueueSave = 0;
  private loadToken = 0;
  private blobTried = new Set<string>();
  private trackCache = new Map<string, Track>();
  private sessionId = uuid();
  private lastActivity = 0;
  private battery: { charging: boolean; level: number } | null = null;
  private started = false;

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
    this.eq = this.profiles[this.outputKey] ?? defaultEq();

    this.setupAudio();
    this.setupMediaSession();
    this.events.startTimer();
    void this.events.flush();
    this.watchEnvironment();
    void this.loadSignals();
    void this.refreshOutputs();

    await this.refreshQueues();
    const qid = prefs.queueId && this.queues.some((q) => q.id === prefs.queueId) ? prefs.queueId : this.queues[0]?.id;
    if (qid) {
      const q = await this.fetchQueue(qid);
      if (q) {
        const idx = q.id === prefs.queueId ? prefs.index : q.index;
        this.queue = { ...q, index: Math.min(idx, Math.max(0, q.tracks.length - 1)) };
        this.viewQueue = this.queue;
        if (this.current) this.loadCurrent(false, q.id === prefs.queueId ? prefs.positionS : 0);
      }
    }
    this.startConnect();
  }

  private setupAudio() {
    const a = new Audio();
    a.preload = 'auto';
    this.audio = a;
    a.volume = 1;
    a.addEventListener('play', () => {
      this.playing = true;
      this.updateMediaSession();
      this.stateChanged();
    });
    a.addEventListener('pause', () => {
      this.playing = false;
      this.updateMediaSession();
      this.stateChanged();
      this.saveLocal();
      void this.saveQueuePosition(true);
    });
    a.addEventListener('waiting', () => (this.buffering = true));
    a.addEventListener('playing', () => (this.buffering = false));
    a.addEventListener('loadedmetadata', () => {
      if (isFinite(a.duration)) this.duration = a.duration;
    });
    a.addEventListener('timeupdate', () => {
      this.position = a.currentTime;
      this.tracker.progress(a.currentTime, this.duration || undefined);
      // A-B repeat
      const { a: A, b: B } = this.ab;
      if (A != null && B != null && a.currentTime >= B) a.currentTime = A;
      if (this.playing && Date.now() - this.lastQueueSave > 15000) void this.saveQueuePosition(false);
    });
    a.addEventListener('ended', () => {
      this.tracker.ended();
      void this.advance(false);
    });
    a.addEventListener('error', () => void this.onAudioError());
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
      const src = this.ctx.createMediaElementSource(this.audio);
      this.graph = buildEqGraph(this.ctx);
      src.connect(this.graph.input);
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
        const a = this.audio as (HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }) | null;
        await a?.setSinkId?.(this.sinkId);
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
      return { id: q.id, name: q.name, slot: q.slot, tracks, index: Math.min(q.currentIndex, Math.max(0, tracks.length - 1)) };
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
    // If the same list is already the queue, just jump.
    if (this.queue && this.queue.tracks.length === tracks.length && this.queue.tracks.every((t, i) => t.id === ids[i])) {
      this.queueMeta.set(this.queue.id, meta);
      await this.playIndex(index);
      return;
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

  private setQueue(q: QueueModel) {
    this.queue = this.shuffle ? { ...q, order: shuffleOrder(q.tracks.length, q.index) } : q;
    // The Queues tab follows the queue that starts playing.
    this.viewQueue = this.queue;
    this.ab = { a: null, b: null };
  }

  /** Switch to a saved queue and play from its current track. */
  async playQueue(id: string, index?: number) {
    if (this.isRemote) {
      this.connect?.command('playQueue', { queueId: id, index: index ?? 0 });
      return;
    }
    const q = id === this.queue?.id ? this.queue : await this.fetchQueue(id);
    if (!q) return;
    this.setQueue({ ...q, index: index ?? q.index });
    await this.playIndex(index ?? q.index);
  }

  async playIndex(i: number, startS = 0) {
    if (!this.queue) return;
    this.queue = { ...this.queue, index: i };
    if (this.viewQueue?.id === this.queue.id) this.viewQueue = this.queue;
    this.claim();
    this.loadCurrent(true, startS);
  }

  /** Load the current track into <audio>. */
  private loadCurrent(autoplay: boolean, startS = 0) {
    const t = this.current;
    const a = this.audio;
    if (!t || !a) return;
    const token = ++this.loadToken;
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
    if (this.audio) this.audio.playbackRate = s;
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
    saveJson(LOCAL_KEY, {
      queueId: this.queue?.id ?? null,
      index: this.queue?.index ?? 0,
      positionS: this.position,
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
          if (s.activeDeviceId && s.activeDeviceId !== this.myDeviceId) void this.loadRemoteTrack(s.trackId);
        },
        onTakeover: (s) => void this.takeover(s),
        onRelease: () => this.release(),
        onCommand: (action, args) => void this.execute(action, args),
        onQueueChanged: (qid) => void this.queueChangedElsewhere(qid),
      },
    });
    this.connect.start();
    let ticks = 0;
    this.stateTimer = setInterval(() => {
      this.remoteNow = Date.now();
      // §5: the active device reports state every 5 s while playing.
      if (++ticks % 10 === 0 && this.playing && this.connect?.isActive) this.reportState();
    }, 500);
    if (typeof window !== 'undefined') {
      window.addEventListener('online', () => this.connect?.kick());
      document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && this.connect?.kick());
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

  private buildState(): PlaybackState {
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
    this.audio?.pause();
    this.remoteState = this.connect?.state ?? this.remoteState;
  }

  /** Commands from a remote, executed here because we're the active device. */
  private async execute(action: CommandAction, args: Record<string, unknown>) {
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
        return this.playQueue(String(args.queueId), Number(args.index ?? 0));
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
