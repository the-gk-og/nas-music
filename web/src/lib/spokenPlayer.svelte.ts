// A separate, simple player for podcasts, audiobooks and internet radio. It owns its own <audio>, pauses
// the music player when it starts (and is paused by SpokenBar when music starts), and syncs resume positions
// through /spoken/progress (an outbox in localStorage covers offline spells). It never posts /events.
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
const SAVE_EVERY_MS = 15_000;
export const SPEEDS = [0.8, 1, 1.1, 1.2, 1.3, 1.5, 1.75, 2, 2.5, 3];

function loadOutbox(): ProgressOutbox {
  try {
    return ProgressOutbox.from(JSON.parse(localStorage.getItem(OUTBOX_KEY) ?? '[]'));
  } catch {
    return new ProgressOutbox();
  }
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
  private npTimer: ReturnType<typeof setInterval> | null = null;
  private sleepTimer: ReturnType<typeof setInterval> | null = null;

  get api() {
    return spokenApi(session.api);
  }

  get chapterIndex(): number {
    return chapterAt((this.current?.chapters ?? []).map((c) => c.start), this.position);
  }

  get live(): boolean {
    return this.current?.kind === 'station';
  }

  private el(): HTMLAudioElement {
    if (this.audio) return this.audio;
    const a = new Audio();
    a.preload = 'auto';
    a.addEventListener('play', () => (this.playing = true));
    a.addEventListener('pause', () => {
      this.playing = false;
      this.save();
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

  async playEpisode(id: string, detail?: EpisodeDetail) {
    try {
      const ep = detail ?? (await this.api.episode(id));
      this.stopStation();
      this.save();
      this.current = {
        kind: 'episode', id: ep.id, title: ep.title, subtitle: ep.podcastTitle ?? '', art: this.api.artSrc('podcast', ep.podcastId, 512),
        duration: ep.durationS, chapters: ep.chapters ?? [], showId: ep.podcastId,
      };
      this.speed = ep.speed || 1;
      const resume = ep.progress && !ep.progress.played ? ep.progress.positionS : 0;
      this.position = resume;
      const a = this.el();
      a.src = this.api.episodeSrc(ep.id);
      a.playbackRate = this.speed;
      await this.seekWhenReady(a, resume);
      await this.start();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async playBook(id: string, detail?: AudiobookDetail, at?: number) {
    try {
      const b = detail ?? (await this.api.book(id));
      this.stopStation();
      this.save();
      this.current = {
        kind: 'book', id: b.id, title: b.title, subtitle: [b.author, b.narrator && `read by ${b.narrator}`].filter(Boolean).join(' · '),
        art: this.api.artSrc('book', b.id, 512), duration: b.durationS,
        chapters: b.chapters.map((c) => ({ start: c.startS, title: c.title })), parts: b.parts,
      };
      this.speed = b.progress?.speed ?? 1;
      const pos = at ?? (b.progress && !b.progress.played ? b.progress.positionS : 0);
      this.position = pos;
      const { index, offset } = partAt(b.parts, pos);
      await this.loadPart(index, offset, true);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async playStation(s: Pick<Station, 'id' | 'name' | 'country' | 'tags'>) {
    this.save();
    this.current = { kind: 'station', id: s.id, title: s.name, subtitle: [s.country, ...s.tags.slice(0, 2)].filter(Boolean).join(' · '),
      art: this.api.artSrc('station', s.id, 256), duration: null, chapters: [] };
    this.speed = 1;
    this.position = 0;
    this.nowPlaying = null;
    const a = this.el();
    a.src = this.api.stationSrc(s.id);
    a.playbackRate = 1;
    await this.start();
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

  toggle() {
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
    void this.seek(this.position + d);
  }

  chapter(delta: 1 | -1) {
    const ch = this.current?.chapters ?? [];
    if (!ch.length) return;
    let i = this.chapterIndex;
    if (delta < 0 && i >= 0 && this.position - ch[i].start > 3) i += 1; // first press: back to this chapter's start
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
