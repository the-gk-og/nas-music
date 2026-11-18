// UI state: hash routing, tabs, sheets/dialogs, toasts.
import type { Track } from './types';

export type TabId = 'queues' | 'now' | 'folders' | 'albums' | 'artists' | 'genres' | 'playlists' | 'search' | 'more';

export const TABS: { id: TabId; label: string }[] = [
  { id: 'queues', label: 'Queues' },
  { id: 'now', label: 'Now playing' },
  { id: 'folders', label: 'Folders' },
  { id: 'albums', label: 'Albums' },
  { id: 'artists', label: 'Artists' },
  { id: 'genres', label: 'Genres' },
  { id: 'playlists', label: 'Playlists' },
  { id: 'search', label: 'Search' },
  { id: 'more', label: 'More' },
];

export interface Route {
  name: string;
  param: string;
}

/** Which tab a page belongs to, for the underline. */
const TAB_OF: Record<string, TabId> = {
  queues: 'queues',
  now: 'now',
  folders: 'folders',
  albums: 'albums',
  album: 'albums',
  artists: 'artists',
  artist: 'artists',
  genres: 'genres',
  genre: 'genres',
  playlists: 'playlists',
  playlist: 'playlists',
  list: 'playlists',
  search: 'search',
};

export function parseHash(hash: string): Route {
  const h = hash.replace(/^#\/?/, '');
  if (!h) return { name: 'now', param: '' };
  const i = h.indexOf('/');
  const name = i < 0 ? h : h.slice(0, i);
  const param = i < 0 ? '' : decodeURIComponent(h.slice(i + 1));
  return { name, param };
}

export function tabOf(r: Route): TabId {
  return TAB_OF[r.name] ?? 'more';
}

export interface SongMenuCtx {
  track: Track;
  /** When opened from a queue row. */
  queueId?: string;
  position?: number;
  /** List the song was in, for "play" actions. */
  list?: Track[];
}

export type Dialog =
  | { kind: 'songMenu'; ctx: SongMenuCtx }
  | { kind: 'songInfo'; trackId: string }
  | { kind: 'addToPlaylist'; tracks: Track[] }
  | { kind: 'addToQueue'; tracks: Track[] }
  | { kind: 'speed' }
  | { kind: 'lyricsEditor'; track: Track; raw: string }
  | { kind: 'devices' }
  | { kind: 'prompt'; title: string; label: string; value: string; ok: string; resolve: (v: string | null) => void }
  | { kind: 'confirm'; title: string; message: string; ok: string; resolve: (v: boolean) => void };

class UiStore {
  route = $state<Route>(parseHash(typeof location !== 'undefined' ? location.hash : ''));
  dialog = $state<Dialog | null>(null);
  toasts = $state<{ id: number; text: string }[]>([]);
  /** Now Playing shows lyrics instead of the cover. */
  lyricsOpen = $state(false);
  lyricsSize = $state(1);
  private toastId = 0;

  constructor() {
    if (typeof window !== 'undefined') window.addEventListener('hashchange', () => (this.route = parseHash(location.hash)));
  }

  get tab(): TabId {
    return tabOf(this.route);
  }

  go(name: string, param = '') {
    const h = '#/' + name + (param ? '/' + encodeURIComponent(param) : '');
    if (location.hash === h) this.route = parseHash(h);
    else location.hash = h;
  }

  back() {
    if (history.length > 1) history.back();
    else this.go('albums');
  }

  open(d: Dialog) {
    this.dialog = d;
  }

  close() {
    const d = this.dialog;
    this.dialog = null;
    if (d?.kind === 'prompt') d.resolve(null);
    if (d?.kind === 'confirm') d.resolve(false);
  }

  prompt(title: string, label: string, value = '', ok = 'OK'): Promise<string | null> {
    return new Promise((resolve) => (this.dialog = { kind: 'prompt', title, label, value, ok, resolve }));
  }

  confirm(title: string, message: string, ok = 'OK'): Promise<boolean> {
    return new Promise((resolve) => (this.dialog = { kind: 'confirm', title, message, ok, resolve }));
  }

  toast(text: string) {
    const id = ++this.toastId;
    this.toasts = [...this.toasts.slice(-2), { id, text }];
    setTimeout(() => (this.toasts = this.toasts.filter((t) => t.id !== id)), 3200);
  }
}

export const ui = new UiStore();
