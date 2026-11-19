// UI state: hash routing, tabs, sheets/dialogs, toasts.
import type { Track } from './types';

export type TabId = 'home' | 'foryou' | 'search' | 'library' | 'queue';

/** v3 destinations (§2): five bottom tabs on phones, a rail on tablets, the sidebar on desktop. */
export const TABS: { id: TabId; label: string }[] = [
  { id: 'home', label: 'Home' },
  { id: 'foryou', label: 'For You' },
  { id: 'search', label: 'Search' },
  { id: 'library', label: 'Library' },
  { id: 'queue', label: 'Queue' },
];

export type PanelId = 'queue' | 'lyrics' | 'devices';

export interface Route {
  name: string;
  param: string;
}

/** Which destination a page belongs to, for the highlight. */
const TAB_OF: Record<string, TabId> = {
  home: 'home',
  foryou: 'foryou',
  mix: 'foryou',
  toget: 'foryou',
  'release-radar': 'foryou',
  wrapped: 'foryou',
  blend: 'foryou',
  search: 'search',
  library: 'library',
  folders: 'library',
  album: 'library',
  artist: 'library',
  genre: 'library',
  playlist: 'library',
  list: 'library',
  queue: 'queue',
  queues: 'queue',
};

export function parseHash(hash: string): Route {
  const h = hash.replace(/^#\/?/, '');
  if (!h) return { name: 'home', param: '' };
  const i = h.indexOf('/');
  const name = i < 0 ? h : h.slice(0, i);
  const param = i < 0 ? '' : decodeURIComponent(h.slice(i + 1));
  return { name, param };
}

export function tabOf(r: Route): TabId | null {
  return TAB_OF[r.name] ?? null;
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
  | { kind: 'tagEdit'; trackIds: string[]; albumId?: string; title: string }
  | { kind: 'playlistShare'; playlistId: string }
  | { kind: 'itemMenu'; item: import('./types').ShelfItem }
  | { kind: 'profile' }
  | { kind: 'catalog'; item: import('./types').CatalogItem }
  | { kind: 'prompt'; title: string; label: string; value: string; ok: string; resolve: (v: string | null) => void }
  | { kind: 'confirm'; title: string; message: string; ok: string; resolve: (v: boolean) => void };

class UiStore {
  route = $state<Route>(parseHash(typeof location !== 'undefined' ? location.hash : ''));
  dialog = $state<Dialog | null>(null);
  toasts = $state<{ id: number; text: string }[]>([]);
  /** Now Playing shows lyrics instead of the cover. */
  lyricsOpen = $state(false);
  lyricsSize = $state(1);
  /** Desktop right panel (Queue / Lyrics / Devices); null = closed. */
  panel = $state<PanelId | null>((() => {
    try {
      const v = localStorage.getItem('nm.panel');
      return v === null ? 'queue' : ((v || null) as PanelId | null);
    } catch {
      return 'queue';
    }
  })());
  /** Ask the search page to focus its box (from `/` or Ctrl+K). */
  searchFocus = $state(0);
  private toastId = 0;

  constructor() {
    if (typeof window !== 'undefined') window.addEventListener('hashchange', () => (this.route = parseHash(location.hash)));
  }

  togglePanel(p: PanelId) {
    this.panel = this.panel === p ? null : p;
    try {
      localStorage.setItem('nm.panel', this.panel ?? '');
    } catch {
      /* ignore */
    }
  }

  focusSearch() {
    if (this.route.name !== 'search') this.go('search');
    this.searchFocus++;
  }

  get tab(): TabId | null {
    return tabOf(this.route);
  }

  go(name: string, param = '') {
    const h = '#/' + name + (param ? '/' + encodeURIComponent(param) : '');
    if (location.hash === h) this.route = parseHash(h);
    else location.hash = h;
  }

  back() {
    if (history.length > 1) history.back();
    else this.go('home');
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
