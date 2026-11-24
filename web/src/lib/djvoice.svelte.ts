// Radio DJ voice (api 0.8.2): the server writes and speaks a short intro before some songs in AI radio and
// mixes; this plays it over the song's opening with the music ducked. Asked for ~20 s before the song so the
// clip is ready (the server synthesises it once and caches it). Only on automatic moves to the next song and
// when this device plays the music itself: a manual skip, pause, casting or a remote silences it.
import { session } from './session.svelte';
import type { DjIntro, DjStatus } from './types';

/** Music level while the DJ talks, and how quickly it dips and comes back (seconds). */
export const DUCK_LEVEL = 0.3;
export const DUCK_DOWN_S = 0.35;
export const DUCK_UP_S = 0.9;
/** Ask for the next song's intro this long before the current one ends. */
export const PREFETCH_S = 25;

export interface IntroSpot {
  queueId: string;
  position: number;
  trackId: string;
  previousTrackId: string | null;
}

/** Radio queues and mixes get a DJ; albums, playlists and search results don't. */
export function djEligible(queue: { radio?: boolean } | null, source: string | undefined): boolean {
  return !!queue && (!!queue.radio || source === 'mix' || source === 'more_like');
}

export function spotKey(s: IntroSpot): string {
  return `${s.queueId}|${s.position}|${s.trackId}|${s.previousTrackId ?? ''}`;
}

class DjVoice {
  status = $state<DjStatus | null>(null);
  /** The words being spoken right now (captions). */
  speaking = $state<string | null>(null);

  private intros = new Map<string, Promise<DjIntro | null>>();
  private el: HTMLAudioElement | null = null;
  private duck: ((level: number, seconds: number) => void) | null = null;
  private endTimer: ReturnType<typeof setTimeout> | null = null;
  /** Bumped by stop(): an intro still being fetched for an older song is dropped. */
  private token = 0;

  get available(): boolean {
    const s = this.status?.state;
    return !!s && s !== 'unavailable' && s !== 'disabled';
  }

  get enabled(): boolean {
    return !!this.status?.enabled && this.available;
  }

  async refresh() {
    try {
      this.status = await session.api.djStatus();
    } catch {
      this.status = null; // older server
    }
  }

  async set(patch: { enabled?: boolean; every?: number }) {
    const settings: Record<string, unknown> = {};
    if (patch.enabled !== undefined) settings['dj.voice'] = patch.enabled;
    if (patch.every !== undefined) settings['dj.every'] = patch.every;
    if (this.status) this.status = { ...this.status, ...patch };
    await session.api.putSettings({ settings });
    await this.refresh();
  }

  /** Ask the server for the intro at this spot (once). Null = the DJ is quiet there, or can't talk. */
  prefetch(spot: IntroSpot): Promise<DjIntro | null> {
    const key = spotKey(spot);
    let p = this.intros.get(key);
    if (!p) {
      p = session.api.djIntro(spot).catch(() => null);
      this.intros.set(key, p);
      if (this.intros.size > 30) this.intros.delete(this.intros.keys().next().value!);
    }
    return p;
  }

  /**
   * A song started on its own (not skipped to): speak its intro, if there is one ready within a second.
   * `duck(level, seconds)` lowers / restores the music; `sinkId` sends the voice to the same output.
   */
  async announce(spot: IntroSpot, duck: (level: number, seconds: number) => void, sinkId = '') {
    if (!this.enabled) return;
    this.stop();
    const token = this.token;
    const intro = await Promise.race([this.prefetch(spot), new Promise<null>((r) => setTimeout(() => r(null), 1500))]);
    if (!intro || token !== this.token) return;
    const src = session.api.mediaUrl(`/${intro.url}`);
    if (!src) return;
    const el = this.el ?? (this.el = new Audio());
    el.src = src;
    el.volume = 1;
    try {
      const s = el as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> };
      if (sinkId && s.setSinkId) await s.setSinkId(sinkId === 'default' ? '' : sinkId);
    } catch {
      /* unsupported */
    }
    if (token !== this.token) return;
    this.duck = duck;
    duck(DUCK_LEVEL, DUCK_DOWN_S);
    this.speaking = intro.text;
    const done = () => this.finish(el);
    el.onended = done;
    el.onerror = done;
    // Never leave the music ducked if the clip stalls.
    this.endTimer = setTimeout(done, intro.durationMs + 4000);
    try {
      await el.play();
    } catch {
      done();
    }
  }

  private finish(el: HTMLAudioElement) {
    if (el !== this.el) return;
    if (this.endTimer) clearTimeout(this.endTimer);
    this.endTimer = null;
    el.onended = el.onerror = null;
    this.speaking = null;
    this.duck?.(1, DUCK_UP_S);
    this.duck = null;
  }

  /** Silence the DJ now (skip, pause, another song, casting). */
  stop() {
    this.token++;
    const el = this.el;
    if (!el) return;
    if (!el.paused) el.pause();
    if (this.speaking !== null || this.duck) this.finish(el);
  }

  /** Forget prefetched intros for a queue that changed (steered radio, edited queue). */
  forget(queueId: string) {
    for (const k of [...this.intros.keys()]) if (k.startsWith(`${queueId}|`)) this.intros.delete(k);
  }
}

export const djVoice = new DjVoice();
