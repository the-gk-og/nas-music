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
  new: 'library',
  folders: 'library',
  album: 'library',
  artist: 'library',
  credits: 'library',
  person: 'library',
  genre: 'library',
  composer: 'library',
  work: 'library',
  playlist: 'library',
  list: 'library',
  smart: 'library',
  'smart-edit': 'library',
  spoken: 'library',
  podcast: 'library',
  book: 'library',
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
  | { kind: 'sleep' }
  | { kind: 'lyricsEditor'; track: Track; raw: string }
  | { kind: 'devices' }
  | { kind: 'jam' }
  | { kind: 'tagEdit'; trackIds: string[]; albumId?: string; title: string }
  | { kind: 'playlistShare'; playlistId: string }
  | { kind: 'itemMenu'; item: import('./types').ShelfItem }
  | { kind: 'profile' }
  | { kind: 'vibe' }
  | { kind: 'catalog'; item: import('./types').CatalogItem }
  | { kind: 'prompt'; title: string; label: string; value: string; ok: string; resolve: (v: string | null) => void }
  | { kind: 'confirm'; title: string; message: string; ok: string; resolve: (v: boolean) => void }
  | { kind: 'pick'; title: string; options: ChooseOption[]; resolve: (v: string | null) => void }
  | { kind: 'choose'; title: string; message: string; options: string[]; resolve: (v: number | null) => void };

export interface ChooseOption {
  value: string;
  label: string;
  /** Indent (folder trees). */
  depth?: number;
  current?: boolean;
}

export interface ToastAction {
  label: string;
  run: () => unknown;
}

export interface Toast {
  id: number;
  text: string;
  action?: ToastAction;
}

class UiStore {
  route = $state<Route>(parseHash(typeof location !== 'undefined' ? location.hash : ''));
  dialog = $state<Dialog | null>(null);
  toasts = $state<Toast[]>([]);
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
    if (d?.kind === 'choose' || d?.kind === 'pick') d.resolve(null);
  }

  /** Pick one of `options` from a list; null = cancelled. */
  pick(title: string, options: ChooseOption[]): Promise<string | null> {
    return new Promise((resolve) => (this.dialog = { kind: 'pick', title, options, resolve }));
  }

  prompt(title: string, label: string, value = '', ok = 'OK'): Promise<string | null> {
    return new Promise((resolve) => (this.dialog = { kind: 'prompt', title, label, value, ok, resolve }));
  }

  confirm(title: string, message: string, ok = 'OK'): Promise<boolean> {
    return new Promise((resolve) => (this.dialog = { kind: 'confirm', title, message, ok, resolve }));
  }

  /** A short message; with `action` it gets a button (e.g. Undo) and stays a little longer. */
  /** Pick one of several buttons; null = cancelled. */
  choose(title: string, message: string, options: string[]): Promise<number | null> {
    return new Promise((resolve) => (this.dialog = { kind: 'choose', title, message, options, resolve }));
  }

  toast(text: string, action?: ToastAction) {
    const id = ++this.toastId;
    this.toasts = [...this.toasts.slice(-2), { id, text, action }];
    setTimeout(() => this.dismissToast(id), action ? 6000 : 3200);
  }

  /** "Removed …" with an Undo button. */
  undoable(text: string, undo: () => unknown) {
    this.toast(text, { label: 'Undo', run: undo });
  }

  dismissToast(id: number) {
    this.toasts = this.toasts.filter((t) => t.id !== id);
  }

  runToast(t: Toast) {
    this.dismissToast(t.id);
    void Promise.resolve()
      .then(() => t.action?.run())
      .catch((e) => this.toast((e as Error).message));
  }
}

export const ui = new UiStore();
