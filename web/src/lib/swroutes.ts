// Which caching strategy the service worker (src/sw.ts) uses for a request. Kept pure so it's testable.
//  - skip: the network handles it, untouched (API, streams, Connect socket, admin pages, other origins)
//  - shell / jam-shell: page navigations; network first, the cached app shell when the server can't be reached
//  - cache-first: content-hashed build assets (/assets/*), which never change under the same name
//  - stale-while-revalidate: the other static files (icons, manifest, worklet), served from cache and refreshed
export type Strategy = 'skip' | 'shell' | 'jam-shell' | 'cache-first' | 'stale-while-revalidate';

export function strategyFor(url: URL, origin: string, method: string, mode: string): Strategy {
  if (method !== 'GET' || url.origin !== origin) return 'skip';
  const p = url.pathname;
  if (p.startsWith('/api/') || p === '/admin' || p.startsWith('/admin/') || p === '/sw.js') return 'skip';
  if (mode === 'navigate') return p.startsWith('/jam/') ? 'jam-shell' : 'shell';
  if (p.startsWith('/assets/')) return 'cache-first';
  return 'stale-while-revalidate';
}

/** Files under web/public/ the shell needs offline (the build adds index.html, jam.html and /assets/*). */
export const PUBLIC_PRECACHE = [
  '/favicon.svg',
  '/manifest.webmanifest',
  '/pitch-worklet.js',
  '/limiter-worklet.js',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/maskable-512.png',
];

/** Build output files worth precaching: everything except source maps and the worker itself. */
export function precacheList(bundleFiles: string[]): string[] {
  const built = bundleFiles.filter((f) => !f.endsWith('.map') && f !== 'sw.js').map((f) => '/' + f.replace(/^\/+/, ''));
  return [...new Set([...built, ...PUBLIC_PRECACHE])].sort();
}
