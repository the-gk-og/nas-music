// Podcasts, audiobooks and internet radio in the web player. Its own <audio> (no crossfade/levelling/events),
// but part of the player's Connect identity (api 0.8, player.spoken bridge): reported as PlaybackState.media,
// controlled from other devices, transferable, and resumed after a reload. As a remote, play* asks the active
// device instead. Resume positions sync through /spoken/progress (an outbox in localStorage covers offline).
// It never posts /events.
import { extrapolatePosition } from './connect';
import { player } from './player.svelte';
import { session } from './session.svelte';
import { ProgressOutbox, chapterAt, partAt, spokenApi, type AudiobookDetail, type Chapter, type EpisodeDetail, type ProgressWrite, type Station } from './spoken';
import { ui } from './ui.svelte';

export type SpokenKind = 'episode' | 'book' | 'station';

export interface SpokenItem {
  kind: SpokenKind;
  id: string;
  title: string;
  subtitle: string;
  art: string;
  duration: number | null;
  chapters: Chapter[];
  parts?: AudiobookDetail['parts'];
  /** podcast id (speed is per show) */
  showId?: string;
}

const OUTBOX_KEY = 'nm.spoken.outbox';
/** What was loaded in this browser (kind, id, position, when), to resume it after a reload. */
const CURRENT_KEY = 'nm.spoken.current';
const SAVE_EVERY_MS = 15_000;
export const SPEEDS = [0.8, 1, 1.1, 1.2, 1.3, 1.5, 1.75, 2, 2.5, 3];

function loadOutbox(): ProgressOutbox {
  try {
    return ProgressOutbox.from(JSON.parse(localStorage.getItem(OUTBOX_KEY) ?? '[]'));
  } catch {
    return new ProgressOutbox();
  }
}

type Api = ReturnType<typeof spokenApi>;

function episodeItem(api: Api, ep: EpisodeDetail): SpokenItem {
  return {
    kind: 'episode', id: ep.id, title: ep.title, subtitle: ep.podcastTitle ?? '', art: api.artSrc('podcast', ep.podcastId, 512),
    duration: ep.durationS, chapters: ep.chapters ?? [], showId: ep.podcastId,
  };
}

function bookItem(api: Api, b: AudiobookDetail): SpokenItem {
  return {
    kind: 'book', id: b.id, title: b.title, subtitle: [b.author, b.narrator && `read by ${b.narrator}`].filter(Boolean).join(' · '),
    art: api.artSrc('book', b.id, 512), duration: b.durationS,
    chapters: b.chapters.map((c) => ({ start: c.startS, title: c.title })), parts: b.parts,
  };
}

function stationItem(api: Api, s: Pick<Station, 'id' | 'name' | 'country' | 'tags'>): SpokenItem {
  return {
    kind: 'station', id: s.id, title: s.name, subtitle: [s.country, ...s.tags.slice(0, 2)].filter(Boolean).join(' · '),
    art: api.artSrc('station', s.id, 256), duration: null, chapters: [],
  };
}

class SpokenPlayer {
  current = $state<SpokenItem | null>(null);
  playing = $state(false);
  loading = $state(false);
  /** Book-wide / episode position in seconds. */
  position = $state(0);
  speed = $state(1);
  nowPlaying = $state<string | null>(null);
  /** Sleep timer: an end time, or 'chapter' (stop at the end of this chapter). */
  sleepAt = $state<number | 'chapter' | null>(null);
  sleepNow = $state(Date.now());
  error = $state('');

  private audio: HTMLAudioElement | null = null;
  private part = 0;
  private lastSave = 0;
  private outbox = loadOutbox();
  private lastLocal = 0;
  /** What the active device plays when this browser is a remote (from PlaybackState.media). */
  remoteItem = $state<SpokenItem | null>(null);
  private remoteKey = '';
  private npTimer: ReturnType<typeof setInterval> | null = null;
  private sleepTimer: ReturnType<typeof setInterval> | null = null;

  get api() {
    return spokenApi(session.api);
  }

  get chapterIndex(): number {
    return chapterAt((this.current?.chapters ?? []).map((c) => c.start), this.position);
  }

  get live(): boolean {
    return this.shownItem?.kind === 'station';
  }

  private el(): HTMLAudioElement {
    if (this.audio) return this.audio;
    const a = new Audio();
    a.preload = 'auto';
    a.addEventListener('play', () => {
      this.playing = true;
      player.spokenChanged(true);
    });
    a.addEventListener('pause', () => {
      this.playing = false;
      this.save();
      player.spokenChanged(false);
    });
    a.addEventListener('waiting', () => (this.loading = true));
    a.addEventListener('playing', () => (this.loading = false));
    a.addEventListener('timeupdate', () => this.onTime());
    a.addEventListener('ended', () => void this.onEnded());
    a.addEventListener('error', () => {
      this.loading = false;
      if (this.current) this.error = this.live ? 'The station is not responding' : 'Could not play this';
    });
    this.audio = a;
    globalThis.addEventListener?.('pagehide', () => {
      this.save();
      void this.flush();
    });
    return a;
  }

  private offsetOfPart(): number {
    return this.current?.parts?.[this.part]?.startS ?? 0;
  }

  private onTime() {
    if (!this.audio || !this.current || this.live) return;
    this.position = this.offsetOfPart() + this.audio.currentTime;
    if (this.sleepAt === 'chapter') {
      const ch = this.current.chapters;
      const i = this.chapterIndex;
      const end = ch[i + 1]?.start ?? this.current.duration ?? Infinity;
      if (this.position >= end - 0.3) {
        this.sleepAt = null;
        this.pause();
      }
    }
    if (Date.now() - this.lastSave > SAVE_EVERY_MS) this.save();
    else if (Date.now() - this.lastLocal > 5000) this.saveCurrent();
  }

  private async onEnded() {
    const c = this.current;
    if (!c) return;
    if (c.kind === 'book' && c.parts && this.part + 1 < c.parts.length) {
      await this.loadPart(this.part + 1, 0, true);
      return;
    }
    if (c.kind !== 'station') {
      this.queueWrite({ kind: c.kind, id: c.id, positionS: c.duration ?? this.position, durationS: c.duration ?? undefined, played: true });
      void this.flush();
    }
  }

  private async loadPart(index: number, offset: number, autoplay: boolean) {
    const c = this.current;
    if (!c || c.kind !== 'book') return;
    this.part = index;
    const a = this.el();
    a.src = this.api.bookPartSrc(c.id, index);
    a.playbackRate = this.speed;
    await this.seekWhenReady(a, offset);
    if (autoplay) await this.start();
  }

  private seekWhenReady(a: HTMLAudioElement, t: number): Promise<void> {
    if (t <= 0) return Promise.resolve();
    return new Promise((res) => {
      const go = () => {
        try {
          a.currentTime = t;
        } catch {
          /* not seekable yet */
        }
        res();
      };
      if (a.readyState >= 1) go();
      else a.addEventListener('loadedmetadata', go, { once: true });
    });
  }

  private async start() {
    const a = this.el();
    if (player.playing) player.pause();
    this.error = '';
    try {
      await a.play();
    } catch (e) {
      if ((e as Error).name !== 'AbortError') this.error = (e as Error).message;
    }
    this.mediaSession();
  }

  // ---------- public ----------

  async playEpisode(id: string, detail?: EpisodeDetail, autoplay = true, at?: number) {
    if (player.isRemote && autoplay) return player.remotePlaySpoken('episode', id, at);
    try {
      const ep = detail ?? (await this.api.episode(id));
      this.stopStation();
      this.save();
      this.current = episodeItem(this.api, ep);
      this.speed = ep.speed || 1;
      const resume = at ?? this.pendingPosition('episode', ep.id) ?? (ep.progress && !ep.progress.played ? ep.progress.positionS : 0);
      this.position = resume;
      const a = this.el();
      a.src = this.api.episodeSrc(ep.id);
      a.playbackRate = this.speed;
      this.saveCurrent();
      await this.seekWhenReady(a, resume);
      if (autoplay) await this.start();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async playBook(id: string, detail?: AudiobookDetail, at?: number, autoplay = true) {
    if (player.isRemote && autoplay) return player.remotePlaySpoken('book', id, at);
    try {
      const b = detail ?? (await this.api.book(id));
      this.stopStation();
      this.save();
      this.current = bookItem(this.api, b);
      this.speed = b.progress?.speed ?? 1;
      const pos = at ?? this.pendingPosition('book', b.id) ?? (b.progress && !b.progress.played ? b.progress.positionS : 0);
      this.position = pos;
      this.saveCurrent();
      const { index, offset } = partAt(b.parts, pos);
      await this.loadPart(index, offset, autoplay);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async playStation(s: Pick<Station, 'id' | 'name' | 'country' | 'tags'>, autoplay = true) {
    if (player.isRemote && autoplay) return player.remotePlaySpoken('station', s.id);
    this.save();
    this.current = stationItem(this.api, s);
    this.speed = 1;
    this.position = 0;
    this.nowPlaying = null;
    const a = this.el();
    a.src = this.api.stationSrc(s.id);
    a.playbackRate = 1;
    this.saveCurrent();
    if (autoplay) await this.start();
    this.stopStation(false);
    const poll = async () => {
      if (!this.current || this.current.kind !== 'station') return;
      try {
        this.nowPlaying = (await this.api.nowPlaying(this.current.id)).title;
        this.mediaSession();
      } catch {
        /* keep the old title */
      }
    };
    void poll();
    this.npTimer = setInterval(poll, 20_000);
  }

  private stopStation(clear = true) {
    if (this.npTimer) clearInterval(this.npTimer);
    this.npTimer = null;
    if (clear) this.nowPlaying = null;
  }

  pause() {
    this.audio?.pause();
  }

  /** What the UI shows: this browser's item, or the one playing on the active device when this is a remote. */
  get remote(): boolean {
    return player.isRemote && !!player.remoteState?.media;
  }

  get shownItem(): SpokenItem | null {
    return this.remote ? this.remoteItem : this.current;
  }

  get shownPlaying(): boolean {
    return this.remote ? !!player.remoteState?.playing : this.playing;
  }

  get shownPosition(): number {
    const s = player.remoteState;
    if (this.remote && s) return extrapolatePosition(s, player.now) / 1000;
    return this.position;
  }

  get shownChapterIndex(): number {
    return chapterAt((this.shownItem?.chapters ?? []).map((c) => c.start), this.shownPosition);
  }

  /** Keep `remoteItem` in step with the active device's PlaybackState.media. */
  async syncRemote(m: { kind: SpokenKind; id: string } | null | undefined) {
    const key = m ? `${m.kind}:${m.id}` : '';
    if (key === this.remoteKey) return;
    this.remoteKey = key;
    if (!m) {
      this.remoteItem = null;
      return;
    }
    try {
      let item: SpokenItem | null = null;
      if (m.kind === 'episode') item = episodeItem(this.api, await this.api.episode(m.id));
      else if (m.kind === 'book') item = bookItem(this.api, await this.api.book(m.id));
      else {
        const st = (await this.api.stations()).find((x) => x.id === m.id);
        if (st) item = stationItem(this.api, st);
      }
      if (this.remoteKey === key) this.remoteItem = item;
    } catch {
      /* keep the old one */
    }
  }

  toggle() {
    if (this.remote) {
      player.toggle();
      return;
    }
    if (!this.current) return;
    if (this.playing) this.pause();
    else if (this.live) {
      // a live stream resumes at "now", not where it was paused
      const a = this.el();
      a.src = this.api.stationSrc(this.current.id);
      void this.start();
    } else void this.start();
  }

  async seek(pos: number) {
    if (this.remote) {
      player.seek(pos);
      return;
    }
    const c = this.current;
    if (!c || this.live || !this.audio) return;
    pos = Math.max(0, Math.min(pos, (c.duration ?? Infinity) - 0.5));
    this.position = pos;
    if (c.kind === 'book' && c.parts) {
      const { index, offset } = partAt(c.parts, pos);
      if (index !== this.part) {
        await this.loadPart(index, offset, this.playing);
        this.save();
        return;
      }
      this.audio.currentTime = offset;
    } else this.audio.currentTime = pos;
    this.save();
  }

  skip(d: number) {
    void this.seek(this.shownPosition + d);
  }

  chapter(delta: 1 | -1) {
    const ch = this.shownItem?.chapters ?? [];
    if (!ch.length) return;
    let i = this.shownChapterIndex;
    if (delta < 0 && i >= 0 && this.shownPosition - ch[i].start > 3) i += 1; // first press: back to this chapter's start
    const t = ch[Math.max(0, Math.min(ch.length - 1, i + delta))];
    if (t) void this.seek(t.start);
  }

  setSpeed(s: number) {
    this.speed = s;
    if (this.audio) this.audio.playbackRate = s;
    const c = this.current;
    if (c?.kind === 'episode' && c.showId) void this.api.patchPodcast(c.showId, { speed: s }).catch(() => undefined);
    if (c?.kind === 'book') this.save();
  }

  setSleep(v: number | 'chapter' | null) {
    this.sleepAt = typeof v === 'number' ? Date.now() + v * 60_000 : v;
    if (this.sleepTimer) clearInterval(this.sleepTimer);
    this.sleepTimer = null;
    if (typeof this.sleepAt === 'number') {
      this.sleepTimer = setInterval(() => {
        this.sleepNow = Date.now();
        if (typeof this.sleepAt === 'number' && Date.now() >= this.sleepAt) {
          this.sleepAt = null;
          if (this.sleepTimer) clearInterval(this.sleepTimer);
          this.pause();
        }
      }, 1000);
    }
  }

  close() {
    if (!this.current) return;
    this.save();
    void this.flush();
    this.stopStation();
    this.setSleep(null);
    if (this.audio) {
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
    }
    this.current = null;
    this.playing = false;
    try {
      localStorage.removeItem(CURRENT_KEY);
    } catch {
      /* ignore */
    }
    player.spokenChanged(false);
  }

  async addBookmark(note?: string | null) {
    const c = this.current;
    if (c?.kind !== 'book') return;
    try {
      await this.api.addBookmark(c.id, this.position, note);
      ui.toast('Bookmark added');
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  // ---------- resume after a reload, Connect ----------

  private saveCurrent() {
    const c = this.current;
    if (!c) return;
    this.lastLocal = Date.now();
    try {
      localStorage.setItem(CURRENT_KEY, JSON.stringify({ kind: c.kind, id: c.id, positionS: this.position, savedAt: this.lastLocal, title: c.title, subtitle: c.subtitle }));
    } catch {
      /* ignore */
    }
  }

  savedAt(): number | null {
    try {
      return (JSON.parse(localStorage.getItem(CURRENT_KEY) ?? 'null') as { savedAt?: number } | null)?.savedAt ?? null;
    } catch {
      return null;
    }
  }

  /** Put back what this browser had loaded (paused, same position). */
  async restore() {
    let saved: { kind: SpokenKind; id: string; positionS: number } | null;
    try {
      saved = JSON.parse(localStorage.getItem(CURRENT_KEY) ?? 'null');
    } catch {
      saved = null;
    }
    if (!saved) return;
    await this.load(saved.kind, saved.id, saved.positionS, false);
  }

  /** Load by kind + id (Connect takeover / playSpoken / restore), here, never as a remote command. */
  async load(kind: SpokenKind, id: string, positionS: number | undefined, play: boolean) {
    if (kind === 'episode') await this.playEpisode(id, undefined, false, positionS);
    else if (kind === 'book') await this.playBook(id, undefined, positionS, false);
    else {
      try {
        const st = (await this.api.stations()).find((x) => x.id === id);
        if (st) await this.playStation(st, false);
      } catch (e) {
        ui.toast((e as Error).message);
      }
    }
    if (play) await this.start();
  }

  /** A position recorded here but not sent yet beats the server's (it is newer). */
  private pendingPosition(kind: 'episode' | 'book', id: string): number | undefined {
    const w = this.outbox.items.get(`${kind}:${id}`);
    if (!w) return undefined;
    return w.played ? 0 : (w.positionS ?? undefined);
  }

  // ---------- progress ----------

  private queueWrite(w: ProgressWrite) {
    this.outbox.add(w);
    try {
      localStorage.setItem(OUTBOX_KEY, JSON.stringify(this.outbox.toJSON()));
    } catch {
      /* storage full / private mode */
    }
  }

  save() {
    const c = this.current;
    if (!c || c.kind === 'station') return;
    this.lastSave = Date.now();
    this.queueWrite({ kind: c.kind, id: c.id, positionS: Math.round(this.position * 10) / 10, durationS: c.duration ?? undefined,
      speed: c.kind === 'book' ? this.speed : undefined });
    void this.flush();
  }

  private flushing = false;
  async flush() {
    if (this.flushing || !this.outbox.items.size || session.isMock) return;
    this.flushing = true;
    const batch = this.outbox.take();
    try {
      await this.api.putProgress(batch);
    } catch {
      this.outbox.restore(batch);
    } finally {
      this.flushing = false;
      try {
        localStorage.setItem(OUTBOX_KEY, JSON.stringify(this.outbox.toJSON()));
      } catch {
        /* ignore */
      }
    }
  }

  private mediaSession() {
    const ms = globalThis.navigator?.mediaSession;
    const c = this.current;
    if (!ms || !c || typeof MediaMetadata === 'undefined') return;
    ms.metadata = new MediaMetadata({ title: this.nowPlaying ?? c.title, artist: this.nowPlaying ? c.title : c.subtitle, artwork: c.art ? [{ src: c.art, sizes: '512x512' }] : [] });
    ms.setActionHandler('play', () => this.toggle());
    ms.setActionHandler('pause', () => this.pause());
    ms.setActionHandler('seekbackward', this.live ? null : () => this.skip(-15));
    ms.setActionHandler('seekforward', this.live ? null : () => this.skip(30));
  }
}

export const spoken = new SpokenPlayer();

player.spoken = {
  active: () => !!spoken.current,
  playing: () => spoken.playing,
  media: () => (spoken.current ? { kind: spoken.current.kind, id: spoken.current.id } : null),
  position: () => spoken.position,
  speed: () => spoken.speed,
  load: (kind, id, positionS, play) => spoken.load(kind, id, positionS, play),
  play: () => {
    if (!spoken.playing) spoken.toggle();
  },
  pause: () => spoken.pause(),
  seek: (p) => void spoken.seek(p),
  setSpeed: (s) => spoken.setSpeed(s),
  close: () => spoken.close(),
  savedAt: () => spoken.savedAt(),
  restore: () => spoken.restore(),
};
