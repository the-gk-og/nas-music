// Service worker for the installable web app (PWA). Built as its own entry to /sw.js (vite.config.ts), which
// also swaps in the precache list and a build version. Registered from main.ts in production builds only.
//
// What it does: keeps the app shell (index.html + the hashed build) so the player opens even when the server
// can't be reached, and shows its own "can't reach your server" banner. It never caches API responses or
// audio: /api/* goes straight to the network.
import { strategyFor } from './lib/swroutes';

declare const __NM_PRECACHE__: string[];
declare const __NM_VERSION__: string;

// Minimal service-worker types (the DOM lib doesn't include the WebWorker ones).
interface ExtendableEvent extends Event {
  waitUntil(p: Promise<unknown>): void;
}
interface FetchEvent extends ExtendableEvent {
  request: Request;
  respondWith(r: Response | Promise<Response>): void;
}
interface SwScope {
  location: Location;
  addEventListener(type: 'install' | 'activate', fn: (e: ExtendableEvent) => void): void;
  addEventListener(type: 'fetch', fn: (e: FetchEvent) => void): void;
  clients: { claim(): Promise<void> };
}

const sw = self as unknown as SwScope;
const PRECACHE: string[] = __NM_PRECACHE__;
const SHELL = `nm-shell-${__NM_VERSION__}`;
const RUNTIME = 'nm-runtime';
/** How long a page load waits for the server before falling back to the cached shell. */
const NAV_TIMEOUT_MS = 4000;

sw.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(PRECACHE.map((u) => new Request(u, { cache: 'reload', credentials: 'same-origin' })))),
  );
});

sw.addEventListener('activate', (e) => {
  e.waitUntil(
    (async () => {
      for (const k of await caches.keys()) if (k.startsWith('nm-shell-') && k !== SHELL) await caches.delete(k);
      // Drop old build assets picked up at runtime; the current ones live in the shell cache.
      const rt = await caches.open(RUNTIME);
      const keep = new Set(PRECACHE);
      for (const req of await rt.keys()) {
        const p = new URL(req.url).pathname;
        if (p.startsWith('/assets/') && !keep.has(p)) await rt.delete(req);
      }
      await sw.clients.claim();
    })(),
  );
});

sw.addEventListener('fetch', (e) => {
  const req = e.request;
  const strategy = strategyFor(new URL(req.url), sw.location.origin, req.method, req.mode);
  switch (strategy) {
    case 'skip':
      return;
    case 'shell':
      return e.respondWith(navigate(req, '/index.html'));
    case 'jam-shell':
      return e.respondWith(navigate(req, '/jam.html'));
    case 'cache-first':
      return e.respondWith(cacheFirst(req));
    case 'stale-while-revalidate':
      return e.respondWith(staleWhileRevalidate(req, e));
  }
});

async function cached(req: Request | string): Promise<Response | undefined> {
  // ignoreVary: the server's CORS layer sends `Vary: Origin`, and module scripts are requested with an Origin
  // header the precache requests didn't have.
  const opts = { ignoreVary: true };
  return (await caches.match(req, { ...opts, cacheName: SHELL })) ?? (await caches.match(req, { ...opts, cacheName: RUNTIME }));
}

async function navigate(req: Request, shell: string): Promise<Response> {
  const timeout = new Promise<null>((r) => setTimeout(() => r(null), NAV_TIMEOUT_MS));
  const net = fetch(req).catch(() => null);
  const res = await Promise.race([net, timeout]);
  // A 5xx is usually the tunnel answering for a server that's down (Cloudflare 502/530).
  if (res && res.status < 500) return res;
  // Unreachable (or slow): the cached shell boots and shows the "can't reach your server" banner.
  const fallback = await cached(shell);
  if (fallback) return fallback;
  return res ?? (await net) ?? new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } });
}

async function cacheFirst(req: Request): Promise<Response> {
  const hit = await cached(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) await (await caches.open(RUNTIME)).put(req, res.clone());
  return res;
}

async function staleWhileRevalidate(req: Request, e: ExtendableEvent): Promise<Response> {
  const hit = await cached(req);
  const refresh = fetch(req)
    .then(async (res) => {
      if (res.ok) await (await caches.open(RUNTIME)).put(req, res.clone());
      return res;
    })
    .catch(() => null);
  if (hit) {
    e.waitUntil(refresh);
    return hit;
  }
  return (await refresh) ?? new Response('', { status: 504 });
}
