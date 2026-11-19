// Appearance settings (docs/v3-ux.md §1, §7): animations, default view, album page style, accent, Home
// shelves. Synced per user via /me/settings (`appearance.*`, `home.shelves`, `home.hidden`); "Only on this
// device" writes `device.<deviceId>.<key>` overrides instead.
import { loadJson, saveJson } from './platform';
import { session } from './session.svelte';
import { HOME_SHELVES, accentColor, effectiveMotion, formFor, type Form, type Motion, type ShelfPrefs } from './v3';

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
  accent: 'green',
  homeShelves: { order: [...HOME_SHELVES], hidden: [] },
};

/** Settings keys (api/openapi.yaml /me/settings). homeShelves is split into order + hidden. */
const KEYS: Record<Exclude<keyof AppearancePrefs, 'homeShelves'>, string> = {
  animations: 'appearance.animations',
  defaultView: 'appearance.defaultView',
  albumStyle: 'appearance.albumPage',
  accent: 'appearance.accent',
};

/** Drop shelf ids from older builds (e.g. "recent") so new ids keep their default order. */
function sanitize(p: AppearancePrefs): AppearancePrefs {
  const known = (ids: unknown) => (Array.isArray(ids) ? ids.filter((x): x is string => typeof x === 'string' && !['radio', 'recent', 'continue'].includes(x)) : []);
  const order = known(p.homeShelves?.order);
  const full = [...order, ...HOME_SHELVES.filter((x) => !order.includes(x))];
  return { ...p, accent: String(p.accent || 'green'), homeShelves: { order: full, hidden: known(p.homeShelves?.hidden) } };
}

function toSettings(k: keyof AppearancePrefs, v: unknown): Record<string, unknown> {
  if (k === 'homeShelves') {
    const p = v as ShelfPrefs;
    return { 'home.shelves': p.order, 'home.hidden': p.hidden };
  }
  return { [KEYS[k]]: v };
}

function fromSettings(all: Record<string, unknown>, prefix: string, base: AppearancePrefs): AppearancePrefs {
  const next = { ...base };
  const get = (k: string) => all[prefix + k];
  for (const [field, key] of Object.entries(KEYS) as [keyof typeof KEYS, string][]) {
    const v = get(key);
    if (v !== undefined && v !== null) (next as Record<string, unknown>)[field] = v;
  }
  const order = get('home.shelves');
  const hidden = get('home.hidden');
  if (Array.isArray(order) || Array.isArray(hidden))
    next.homeShelves = { order: Array.isArray(order) ? (order as string[]) : base.homeShelves.order, hidden: Array.isArray(hidden) ? (hidden as string[]) : base.homeShelves.hidden };
  return next;
}

class Appearance {
  prefs = $state<AppearancePrefs>(sanitize({ ...DEFAULTS, ...loadJson<Partial<AppearancePrefs>>('nm.appearance', {}) }));
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
    // Library chips are remembered per device on the server too (`device.<id>.library.view.<chip>`).
    const chip = /^lib\.(.+)$/.exec(section)?.[1];
    const dev = session.saved.deviceId;
    if (chip && dev) void session.api.putSettings({ settings: { [`device.${dev}.library.view.${chip}`]: v } }).catch(() => {});
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
    root.style.setProperty('--accent', accentColor(this.prefs.accent));
  }

  private get devicePrefix(): string {
    return session.saved.deviceId ? `device.${session.saved.deviceId}.` : '';
  }

  /** User values, then this device's overrides when "Only on this device" is on. */
  async load() {
    try {
      const { settings } = await session.api.getSettings();
      let next = fromSettings(settings ?? {}, '', { ...DEFAULTS, ...this.prefs });
      const pre = this.devicePrefix;
      if (pre) {
        const hasOverride = Object.keys(settings ?? {}).some((k) => k.startsWith(pre + 'appearance.') || k.startsWith(pre + 'home.'));
        if (hasOverride) {
          this.localOnly = true;
          saveJson('nm.appearance.local', { on: true });
        }
        if (this.localOnly) next = fromSettings(settings ?? {}, pre, next);
      }
      this.prefs = sanitize(next);
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
    const body = toSettings(k, v);
    const pre = this.localOnly ? this.devicePrefix : '';
    try {
      await session.api.putSettings({ settings: Object.fromEntries(Object.entries(body).map(([key, val]) => [pre + key, val])) });
    } catch {
      /* saved locally */
    }
  }

  /** On: write this device's overrides. Off: delete them (null) and push the values to the user level. */
  async setLocalOnly(on: boolean) {
    const pre = this.devicePrefix;
    this.localOnly = on;
    saveJson('nm.appearance.local', { on });
    const all: Record<string, unknown> = {};
    for (const k of Object.keys(DEFAULTS) as (keyof AppearancePrefs)[]) Object.assign(all, toSettings(k, this.prefs[k]));
    const body: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(all)) {
      if (on) body[pre + key] = val;
      else {
        body[key] = val;
        if (pre) body[pre + key] = null;
      }
    }
    await session.api.putSettings({ settings: body }).catch(() => {});
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
