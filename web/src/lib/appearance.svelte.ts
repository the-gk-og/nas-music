// Appearance settings (docs/v3-ux.md §1, §7): animations, default view, album page style, accent, Home
// shelves. Synced per user via /me/settings (`ui.*` keys); "Only on this device" keeps a local override.
import { loadJson, saveJson } from './platform';
import { session } from './session.svelte';
import { ACCENTS, HOME_SHELVES, effectiveMotion, formFor, type Form, type Motion, type ShelfPrefs } from './v3';

export type ViewMode = 'grid' | 'list';
export type PageStyle = 'big' | 'simple';

export interface AppearancePrefs {
  animations: Motion;
  defaultView: ViewMode;
  albumStyle: PageStyle;
  accent: string;
  homeShelves: ShelfPrefs;
}

const DEFAULTS: AppearancePrefs = {
  animations: 'quick',
  defaultView: 'grid',
  albumStyle: 'big',
  accent: 'Green',
  homeShelves: { order: [...HOME_SHELVES], hidden: [] },
};

const KEYS: Record<keyof AppearancePrefs, string> = {
  animations: 'ui.animations',
  defaultView: 'ui.defaultView',
  albumStyle: 'ui.albumStyle',
  accent: 'ui.accent',
  homeShelves: 'ui.homeShelves',
};

class Appearance {
  prefs = $state<AppearancePrefs>({ ...DEFAULTS, ...loadJson<Partial<AppearancePrefs>>('nm.appearance', {}) });
  localOnly = $state<boolean>(loadJson('nm.appearance.local', { on: false }).on);
  reducedMotion = $state(false);
  width = $state(typeof window !== 'undefined' ? window.innerWidth : 1280);
  height = $state(typeof window !== 'undefined' ? window.innerHeight : 800);
  /** Per-section grid/list choice, remembered on this device. */
  views = $state<Record<string, ViewMode>>(loadJson('nm.views', {}));
  pageStyles = $state<Record<string, PageStyle>>(loadJson('nm.pageStyles', {}));

  constructor() {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    this.reducedMotion = !!mq?.matches;
    mq?.addEventListener?.('change', (e) => (this.reducedMotion = e.matches));
    window.addEventListener('resize', () => {
      this.width = window.innerWidth;
      this.height = window.innerHeight;
    });
    this.apply();
  }

  get motion(): Motion {
    return effectiveMotion(this.prefs.animations, this.reducedMotion);
  }

  get form(): Form {
    return formFor(this.width);
  }

  view(section: string): ViewMode {
    return this.views[section] ?? this.prefs.defaultView;
  }

  setView(section: string, v: ViewMode) {
    this.views = { ...this.views, [section]: v };
    saveJson('nm.views', this.views);
  }

  pageStyle(kind: 'album' | 'artist' | 'playlist'): PageStyle {
    return this.pageStyles[kind] ?? this.prefs.albumStyle;
  }

  setPageStyle(kind: 'album' | 'artist' | 'playlist', s: PageStyle) {
    this.pageStyles = { ...this.pageStyles, [kind]: s };
    saveJson('nm.pageStyles', this.pageStyles);
  }

  apply() {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    root.dataset.motion = this.motion;
    root.style.setProperty('--accent', ACCENTS[this.prefs.accent] ?? ACCENTS.Green);
  }

  /** Server values, unless this device keeps its own. */
  async load() {
    if (this.localOnly) return this.apply();
    try {
      const { settings } = await session.api.getSettings();
      const next = { ...this.prefs };
      for (const [k, key] of Object.entries(KEYS) as [keyof AppearancePrefs, string][]) {
        if (settings?.[key] !== undefined && settings[key] !== null) (next as Record<string, unknown>)[k] = settings[key];
      }
      this.prefs = next;
      saveJson('nm.appearance', this.prefs);
    } catch {
      /* offline: keep local */
    }
    this.apply();
  }

  async set<K extends keyof AppearancePrefs>(k: K, v: AppearancePrefs[K]) {
    this.prefs = { ...this.prefs, [k]: v };
    saveJson('nm.appearance', this.prefs);
    this.apply();
    if (this.localOnly) return;
    try {
      await session.api.putSettings({ settings: { [KEYS[k]]: v } });
    } catch {
      /* saved locally */
    }
  }

  setLocalOnly(on: boolean) {
    this.localOnly = on;
    saveJson('nm.appearance.local', { on });
    if (!on) for (const k of Object.keys(KEYS) as (keyof AppearancePrefs)[]) void this.set(k, this.prefs[k]);
  }
}

export const appearance = new Appearance();

$effect.root(() => {
  $effect(() => {
    void appearance.motion;
    void appearance.prefs.accent;
    appearance.apply();
  });
});

/** Run a DOM update inside a View Transition when motion is on (cover grows into Now Playing, fades). */
export function withTransition(update: () => void) {
  const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
  if (appearance.motion === 'off' || !doc.startViewTransition) return update();
  doc.startViewTransition(update);
}
