// Guest-side HTTP for the Jam page: same origin, guest token in `X-Jam-Token` (never a session token).
import type { JamPreview, JamRequest, JamSnapshot, JamTrack } from '../lib/jam';

export class JamHttpError extends Error {
  constructor(public status: number, detail: string) {
    super(detail || `HTTP ${status}`);
  }
}

const BASE = '/api/v1';

async function call<T>(method: string, path: string, token: string | null, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['X-Jam-Token'] = token;
  // Cookies go along only for joining as yourself (`asMember`); the server ignores them elsewhere on /jam.
  const res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (typeof j?.detail === 'string') detail = j.detail;
    } catch {
      /* not JSON */
    }
    throw new JamHttpError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const guestApi = {
  preview: (code: string) => call<JamPreview>('GET', `/jam/join/${encodeURIComponent(code)}`, null),
  join: (code: string, name: string | null, asMember = false) =>
    call<JamSnapshot & { token: string }>('POST', `/jam/join/${encodeURIComponent(code)}`, null, asMember ? { asMember: true } : { name }),
  me: (t: string) => call<JamSnapshot>('GET', '/jam/me', t),
  leave: (t: string) => call<void>('DELETE', '/jam/me', t),
  search: (t: string, q: string) => call<{ tracks: JamTrack[] }>('GET', `/jam/search?q=${encodeURIComponent(q)}`, t),
  add: (t: string, trackId: string, playNext: boolean) =>
    call<{ request: JamRequest; queued: boolean }>('POST', '/jam/requests', t, { trackId, playNext }),
  withdraw: (t: string, id: string) => call<void>('DELETE', `/jam/requests/${encodeURIComponent(id)}`, t),
  vote: (t: string, trackId: string, value: -1 | 0 | 1) => call<void>('POST', '/jam/votes', t, { trackId, value }),
  react: (t: string, emoji: string) => call<void>('POST', '/jam/reactions', t, { emoji }),
  artUrl: (t: string, albumId: string, size = 256) =>
    `${BASE}/jam/art/${encodeURIComponent(albumId)}?size=${size}&token=${encodeURIComponent(t)}`,
  wsUrl: (t: string) => `${location.origin.replace(/^http/, 'ws')}${BASE}/jam/ws?token=${encodeURIComponent(t)}`,
};
