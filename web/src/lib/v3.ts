// Pure helpers for the v3 redesign (docs/v3-ux.md): screen forms, motion, shelves, radio, gestures.
import type { RadioSeed, Shelf } from './types';

// ---- screen forms (§6) ----
export type Form = 'phone' | 'tablet' | 'desktop';

export function formFor(width: number): Form {
  if (width >= 1200) return 'desktop';
  if (width >= 600) return 'tablet';
  return 'phone';
}

/** Tablet landscape shows Now Playing (or the Queue) as a side pane. */
export function tabletSidePane(width: number, height: number): boolean {
  return formFor(width) === 'tablet' && width > height && width >= 900;
}

// ---- motion (§1) ----
export type Motion = 'off' | 'quick' | 'expressive';

/** The OS "reduce motion" setting wins over the user's choice. */
export function effectiveMotion(chosen: Motion, prefersReduced: boolean): Motion {
  return prefersReduced ? 'off' : chosen;
}

export function motionMs(m: Motion, kind: 'fade' | 'cover' = 'fade'): number {
  if (m === 'off') return 0;
  if (m === 'quick') return kind === 'cover' ? 200 : 150;
  return kind === 'cover' ? 420 : 300;
}

// ---- Home shelves (§3.1): user order + hidden, empty ones hide ----
/** Home shelf ids (= kinds) as stored in settings `home.shelves` / `home.hidden`. */
export const HOME_SHELVES = ['start_radio', 'devices', 'recently_played', 'continue_listening', 'pinned'] as const;
export const HOME_SHELF_TITLES: Record<string, string> = {
  start_radio: 'Start radio',
  devices: 'Your devices',
  recently_played: 'Recently played',
  continue_listening: 'Continue listening',
  pinned: 'Pinned',
};

export interface ShelfPrefs {
  order: string[];
  hidden: string[];
}

/** Shelves that render their own content and may arrive with no items. */
const SELF_RENDERED = new Set(['start_radio', 'devices']);

/**
 * The server already orders/hides shelves from `home.shelves` / `home.hidden`; doing it again here makes a
 * just-changed setting (or the mock / an older server) look right immediately. Matches by id, then by kind.
 */
export function arrangeShelves(shelves: Shelf[], prefs: ShelfPrefs, extraNonEmpty: Set<string> = new Set()): Shelf[] {
  const keys = (s: Shelf) => [s.id, s.kind];
  const rank = (s: Shelf) => {
    const i = Math.min(...keys(s).map((k) => (prefs.order.includes(k) ? prefs.order.indexOf(k) : 1000)));
    return i;
  };
  return shelves
    .filter((s) => !keys(s).some((k) => prefs.hidden.includes(k)))
    .filter((s) => s.items.length > 0 || (SELF_RENDERED.has(s.kind) && (s.kind !== 'devices' || extraNonEmpty.has('devices'))))
    .map((s, i) => ({ s, i }))
    .sort((a, b) => rank(a.s) - rank(b.s) || a.i - b.i)
    .map((x) => x.s);
}

export function moveInList<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const out = list.slice();
  const [x] = out.splice(from, 1);
  out.splice(to, 0, x);
  return out;
}

// ---- radio (§5) ----
export const RADIO_TOPUP_AT = 5;

/** Ask for more when 5 or fewer tracks remain after the current one. */
export function needsTopUp(len: number, index: number, threshold = RADIO_TOPUP_AT): boolean {
  return len - index - 1 <= threshold;
}

/** A radio card's seed: the item's `seed`, else ids like `artist:ar2`, `track:t1`, or `random`. */
export function parseRadioId(id: string, seed?: RadioSeed | null): RadioSeed {
  if (seed?.type) return seed;
  const i = id.indexOf(':');
  if (id === 'random' || i < 0) return { type: 'random' };
  const type = id.slice(0, i);
  if (type === 'track' || type === 'artist' || type === 'album' || type === 'playlist') return { type, id: id.slice(i + 1) };
  return { type: 'random' };
}

/** Merge a topped-up server queue into the local copy without disturbing what's already there. */
export function appendNew<T extends { id: string }>(local: T[], server: T[]): T[] {
  if (server.length <= local.length) return local;
  const samePrefix = local.every((t, i) => server[i]?.id === t.id);
  return samePrefix ? [...local, ...server.slice(local.length)] : [...local, ...server.filter((t) => !local.some((l) => l.id === t.id))];
}

// ---- gestures (§3.5, §2) ----
export type Swipe = 'left' | 'right' | 'up' | 'down' | 'tap' | null;

export function classifySwipe(dx: number, dy: number, ms: number, min = 50): Swipe {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax < 10 && ay < 10) return ms < 500 ? 'tap' : null;
  if (Math.max(ax, ay) < min) return null;
  if (ax > ay * 1.3) return dx < 0 ? 'left' : 'right';
  if (ay > ax * 1.3) return dy < 0 ? 'up' : 'down';
  return null;
}

// ---- greeting ----
export function greeting(hour: number): string {
  if (hour < 5) return 'Good night';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

// ---- accent presets (§7; settings `appearance.accent`: a preset name or "#rrggbb") ----
export const ACCENTS: Record<string, string> = {
  green: '#4ade80',
  blue: '#60a5fa',
  purple: '#c084fc',
  pink: '#f472b6',
  orange: '#fb923c',
  red: '#f87171',
  teal: '#2dd4bf',
};

export function accentColor(v: string | null | undefined): string {
  if (v && /^#[0-9a-f]{6}$/i.test(v)) return v;
  return ACCENTS[(v ?? '').toLowerCase()] ?? ACCENTS.green;
}

/** Progress (0..1) for a "Continue listening" item. */
export function progressFraction(p: { index?: number; count?: number } | null | undefined): number | null {
  if (!p || !p.count) return null;
  return Math.max(0, Math.min(1, (p.index ?? 0) / p.count));
}

/** Normalised key for owned/not-owned matching when there's no MBID. */
export function matchKey(artist: string, title: string): string {
  const norm = (s: string) =>
    s
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/\(.*?\)|\[.*?\]/g, '')
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  return `${norm(artist)}|${norm(title)}`;
}
