import type { DeviceKind } from './types';

export const APP_VERSION = '0.1.0';

export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export function deviceKind(): DeviceKind {
  return isTauri() ? 'desktop' : 'web';
}

/** "Firefox on Linux", "Chrome on Windows", "Homebeat desktop (macOS)". */
export function defaultDeviceName(ua: string = typeof navigator !== 'undefined' ? navigator.userAgent : ''): string {
  const os = /Android/.test(ua)
    ? 'Android'
    : /iPhone|iPad/.test(ua)
      ? 'iOS'
      : /Windows/.test(ua)
        ? 'Windows'
        : /Mac OS X|Macintosh/.test(ua)
          ? 'macOS'
          : /CrOS/.test(ua)
            ? 'ChromeOS'
            : /Linux/.test(ua)
              ? 'Linux'
              : 'unknown OS';
  if (isTauri()) return `Desktop app on ${os}`;
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /Firefox\//.test(ua)
      ? 'Firefox'
      : /OPR\//.test(ua)
        ? 'Opera'
        : /Chrome\//.test(ua)
          ? 'Chrome'
          : /Safari\//.test(ua)
            ? 'Safari'
            : 'Browser';
  return `${browser} on ${os}`;
}

/** Safari can't play Ogg Opus in <audio>; ask for FLAC there (§8). */
export function preferredFormat(): 'opus' | 'flac' {
  if (typeof document === 'undefined') return 'opus';
  const a = document.createElement('audio');
  return a.canPlayType('audio/ogg; codecs="opus"') ? 'opus' : 'flac';
}

export const storage: Storage | null = (() => {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null;
  }
})();

export function loadJson<T>(key: string, fallback: T): T {
  try {
    const raw = storage?.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}

export function saveJson(key: string, v: unknown) {
  try {
    storage?.setItem(key, JSON.stringify(v));
  } catch {
    /* ignore */
  }
}
