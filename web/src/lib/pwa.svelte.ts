// Installable web app: service worker registration, the "Install app" prompt, and whether the server can
// be reached (the offline banner). The worker itself is src/sw.ts.
import { isTauri } from './platform';
import { session } from './session.svelte';

interface InstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

/** Retry delays while the server is unreachable: quick at first, then every minute. */
export const RETRY_MS = [3000, 5000, 10000, 20000, 30000, 60000];
export function retryDelay(attempt: number): number {
  return RETRY_MS[Math.min(Math.max(attempt, 0), RETRY_MS.length - 1)];
}

class Pwa {
  /** The browser offered installation (Chromium: beforeinstallprompt). */
  canInstall = $state(false);
  /** Running as an installed app window. */
  installed = $state(false);
  /** null until the first check. */
  reachable = $state<boolean | null>(null);
  checking = $state(false);
  private deferred: InstallPromptEvent | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private onBack: (() => void) | null = null;
  private watching = false;

  init() {
    if (typeof window === 'undefined') return;
    this.installed = window.matchMedia?.('(display-mode: standalone)').matches ?? false;
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      this.deferred = e as InstallPromptEvent;
      this.canInstall = true;
    });
    window.addEventListener('appinstalled', () => {
      this.deferred = null;
      this.canInstall = false;
      this.installed = true;
    });
    if (import.meta.env.PROD && !isTauri() && 'serviceWorker' in navigator && window.isSecureContext) {
      // A LAN address with an untrusted self-signed certificate refuses service workers: then the app
      // just isn't installable there (the tunnel address or a trusted certificate works).
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((e) => console.info('Service worker not registered:', e));
      });
    }
  }

  async install() {
    const d = this.deferred;
    if (!d) return;
    await d.prompt();
    const { outcome } = await d.userChoice;
    if (outcome === 'accepted') this.canInstall = false;
    this.deferred = null;
  }

  /** Start watching the server once signed in. `onBack` runs when it comes back after being unreachable. */
  watch(onBack: () => void) {
    this.onBack = onBack;
    if (typeof window === 'undefined' || this.watching) return;
    this.watching = true;
    window.addEventListener('online', () => void this.check());
    window.addEventListener('offline', () => this.setReachable(false));
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.reachable === false) void this.check();
    });
    void this.check();
  }

  async check() {
    if (this.checking) return;
    this.checking = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    let ok: boolean;
    try {
      ok = typeof navigator === 'undefined' || navigator.onLine !== false ? !!(await session.api.health()) : false;
    } catch {
      ok = false;
    }
    this.checking = false;
    this.setReachable(ok);
  }

  private setReachable(ok: boolean) {
    const was = this.reachable;
    this.reachable = ok;
    if (ok) {
      this.attempt = 0;
      if (was === false) this.onBack?.();
      return;
    }
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.check(), retryDelay(this.attempt++));
  }
}

export const pwa = new Pwa();
