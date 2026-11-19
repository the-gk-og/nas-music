// Listening settings (per device, localStorage): gapless/crossfade, volume levelling (ReplayGain),
// and the sleep timer's pure rules. The audio engine that uses them lives in player.svelte.ts.
import type { QueueModel } from './queue';
import type { Track } from './types';

export type LevellingMode = 'off' | 'track' | 'album' | 'auto';

export interface ListeningSettings {
  /** 0 = gapless (no fade); 1–12 s crossfade between tracks. */
  crossfadeS: number;
  levelling: LevellingMode;
  /** Added to every ReplayGain value, −15…+15 dB. */
  preampDb: number;
  /** Limit the gain so peak × gain ≤ 1. */
  preventClipping: boolean;
}

export const CROSSFADE_MAX_S = 12;
/** How long before the end of a track the next one is loaded into the second <audio>. */
export const PRELOAD_AHEAD_S = 30;
/** Fade-out length when the sleep timer ends. */
export const SLEEP_FADE_S = 10;

export function defaultListening(): ListeningSettings {
  return { crossfadeS: 0, levelling: 'auto', preampDb: 0, preventClipping: true };
}

const KEY = 'nm.listening';

export function loadListening(storage: Pick<Storage, 'getItem'> | null): ListeningSettings {
  try {
    const raw = storage?.getItem(KEY);
    return normaliseListening(raw ? (JSON.parse(raw) as Partial<ListeningSettings>) : {});
  } catch {
    return defaultListening();
  }
}

export function saveListening(storage: Pick<Storage, 'setItem'> | null, s: ListeningSettings) {
  try {
    storage?.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage full / blocked */
  }
}

export function normaliseListening(p: Partial<ListeningSettings>): ListeningSettings {
  const d = defaultListening();
  const num = (v: unknown, lo: number, hi: number, dflt: number) => (typeof v === 'number' && isFinite(v) ? Math.max(lo, Math.min(hi, v)) : dflt);
  return {
    crossfadeS: num(p.crossfadeS, 0, CROSSFADE_MAX_S, d.crossfadeS),
    levelling: (['off', 'track', 'album', 'auto'] as const).includes(p.levelling as LevellingMode) ? (p.levelling as LevellingMode) : d.levelling,
    preampDb: num(p.preampDb, -15, 15, d.preampDb),
    preventClipping: typeof p.preventClipping === 'boolean' ? p.preventClipping : d.preventClipping,
  };
}

// ---------- albums in order ----------

/** `b` directly follows `a` on the same album (track n → n+1, or last of a disc → first of the next). */
export function followsOnAlbum(a: Track | null | undefined, b: Track | null | undefined): boolean {
  if (!a || !b || a.albumId !== b.albumId || a.id === b.id) return false;
  const ta = a.trackNo, tb = b.trackNo;
  if (ta == null || tb == null) return true; // untagged numbers: same album is the best we know
  const da = a.discNo ?? 1, db = b.discNo ?? 1;
  return (da === db && tb === ta + 1) || (db === da + 1 && tb === 1);
}

/** Is the queue playing an album in order around `index` (for "auto" levelling)? */
export function playingAlbumInOrder(q: QueueModel | null, index: number): boolean {
  if (!q || q.order) return false; // shuffle: never album mode
  const t = q.tracks[index];
  return followsOnAlbum(q.tracks[index - 1], t) || followsOnAlbum(t, q.tracks[index + 1]);
}

/** Crossfade length for an automatic transition a → b. Consecutive album tracks are kept gapless
 *  (live albums, DJ mixes, classical), and so is repeat-one. */
export function crossfadeFor(s: ListeningSettings, a: Track | null, b: Track | null): number {
  if (!a || !b || s.crossfadeS <= 0 || a.id === b.id) return 0;
  if (followsOnAlbum(a, b)) return 0;
  const shortest = Math.min(a.durationS || Infinity, b.durationS || Infinity);
  // Never fade over more than a third of a short track.
  return Math.max(0, Math.min(s.crossfadeS, shortest / 3));
}

// ---------- ReplayGain ----------

export const dbToLinear = (db: number) => Math.pow(10, db / 20);

/**
 * Linear gain for a track. `albumMode` = use album gain (falls back to track gain). Tracks the server
 * hasn't levelled yet get the preamp only. Never returns more than +24 dB worth.
 */
export function levellingGain(t: Track | null | undefined, s: ListeningSettings, albumMode: boolean): number {
  if (!t || s.levelling === 'off') return 1;
  const rg = t.replayGain;
  let db = s.preampDb;
  let peak: number | null | undefined = null;
  if (rg) {
    const useAlbum = albumMode && rg.albumGain != null;
    db += useAlbum ? rg.albumGain! : rg.trackGain;
    peak = useAlbum ? (rg.albumPeak ?? rg.trackPeak) : rg.trackPeak;
  }
  let g = dbToLinear(Math.max(-40, Math.min(24, db)));
  if (s.preventClipping && peak && peak > 0) g = Math.min(g, 1 / peak);
  return g;
}

export function useAlbumGain(s: ListeningSettings, q: QueueModel | null, index: number): boolean {
  return s.levelling === 'album' || (s.levelling === 'auto' && playingAlbumInOrder(q, index));
}

// ---------- sleep timer ----------

export type SleepMode = 'minutes' | 'track' | 'album';

export interface SleepTimer {
  mode: SleepMode;
  /** Epoch ms when playback stops (mode 'minutes'). */
  endsAt?: number;
  /** Album being finished (mode 'album'). */
  albumId?: string;
}

/** Should playback stop instead of moving from `cur` to `next`? */
export function sleepStopsAt(timer: SleepTimer | null, cur: Track | null, next: Track | null): boolean {
  if (!timer) return false;
  if (timer.mode === 'track') return true;
  if (timer.mode === 'album') return !next || next.albumId !== (timer.albumId ?? cur?.albumId);
  return false;
}

/** Volume multiplier (1 → 0) during the sleep fade-out. `remainingS` = seconds until the stop. */
export function sleepFadeLevel(remainingS: number, fadeS = SLEEP_FADE_S): number {
  if (remainingS >= fadeS) return 1;
  if (remainingS <= 0) return 0;
  const x = remainingS / fadeS;
  return x * x; // ease out: quiet sooner, so the end is barely audible
}

export function sleepLabel(timer: SleepTimer | null, now = Date.now()): string {
  if (!timer) return 'Off';
  if (timer.mode === 'track') return 'End of track';
  if (timer.mode === 'album') return 'End of album';
  const m = Math.max(0, Math.ceil(((timer.endsAt ?? now) - now) / 60000));
  return `${m} min`;
}

/** Equal-power crossfade curves, x = 0…1 through the fade. */
export function fadeIn(x: number): number {
  return Math.sin((Math.max(0, Math.min(1, x)) * Math.PI) / 2);
}
export function fadeOut(x: number): number {
  return Math.cos((Math.max(0, Math.min(1, x)) * Math.PI) / 2);
}
