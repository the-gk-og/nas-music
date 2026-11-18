// In-browser mock of the v2 server (HTTP + Connect hub), for `npm run dev:mock` and the server address "mock".
// Login: admin / admin123 (admin) — or register with invite code WELCOME1.
import type { SocketLike } from '../connect';
import { parseLrc } from '../lyrics';
import type {
  ClientMessage,
  ConnectDevice,
  Invite,
  PlaybackState,
  Playlist,
  Queue,
  Session,
  Track,
  TrackInfo,
} from '../types';
import type { MediaResolver } from './client';
import { artSvg, buildLibrary, mockMixes, mockSuggestions, toneWav } from './mockData';

interface MockUser {
  id: string;
  username: string;
  password: string;
  isAdmin: boolean;
  disabled: boolean;
  createdAt: string;
}

const iso = () => new Date().toISOString();
let seq = 0;
const nid = (p: string) => `${p}${Date.now().toString(36)}${(seq++).toString(36)}`;

class HttpError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

export class MockBackend {
  lib = buildLibrary();
  users: MockUser[] = [{ id: 'u1', username: 'admin', password: 'admin123', isAdmin: true, disabled: false, createdAt: iso() }];
  tokens = new Map<string, { userId: string; deviceId: string }>();
  invites: Invite[] = [{ code: 'WELCOME1', note: 'mock invite', createdAt: iso(), expiresAt: new Date(Date.now() + 7 * 864e5).toISOString(), url: '/join/WELCOME1' }];
  playlists: (Playlist & { trackIds: string[] })[] = [
    { id: 'p1', name: 'car', trackIds: ['t0_1', 't3_2', 't5_1'], createdAt: iso(), updatedAt: iso() },
    { id: 'p2', name: 'nerdcore', trackIds: ['t1_1', 't1_2'], createdAt: iso(), updatedAt: iso() },
  ];
  queues: (Queue & { trackIds: string[] })[] = [];
  lyricOverrides = new Map<string, string>();
  settings: Record<string, unknown> = {};
  played = new Map<string, { count: number; last: string }>();
  events: unknown[] = [];
  mixes = mockMixes(this.lib.tracks);
  latencyMs = 60;
  hub = new MockHub(this);

  constructor() {
    const t = this.lib.tracks;
    this.queues.push({
      id: 'q1',
      name: 'Search: conver',
      slot: 20,
      currentIndex: 0,
      positionMs: 0,
      trackIds: t.filter((x) => x.title.startsWith('Conversations')).map((x) => x.id).slice(0, 4),
      updatedAt: iso(),
    });
    if (!this.queues[0].trackIds.length) this.queues[0].trackIds = t.slice(0, 3).map((x) => x.id);
    this.queues.push({ id: 'q2', name: 'All songs', slot: 1, currentIndex: 0, positionMs: 0, trackIds: t.slice(0, 25).map((x) => x.id), updatedAt: iso() });
  }

  private track(id: string): TrackInfo {
    const t = this.lib.tracks.find((x) => x.id === id);
    if (!t) throw new HttpError(404, 'track not found');
    return t;
  }
  private pub(t: TrackInfo): Track {
    const { id, title, artistId, artist, albumId, album, trackNo, discNo, year, genre, durationS, flacBytes, starred, rating } = t;
    return { id, title, artistId, artist, albumId, album, trackNo, discNo, year, genre, durationS, flacBytes, starred, rating };
  }
  private tracksOf(ids: string[]): Track[] {
    return ids.map((id) => this.lib.tracks.find((t) => t.id === id)).filter((t): t is TrackInfo => !!t).map((t) => this.pub(t));
  }
  private session(u: MockUser, deviceName: string): Session {
    const deviceId = nid('d');
    const token = nid('tok');
    this.tokens.set(token, { userId: u.id, deviceId });
    this.hub.registerDevice(deviceId, deviceName);
    return { userId: u.id, username: u.username, isAdmin: u.isAdmin, deviceId, deviceToken: token };
  }
  userFor(token: string | null) {
    const s = token ? this.tokens.get(token) : undefined;
    return s ? { ...s, user: this.users.find((u) => u.id === s.userId)! } : null;
  }
  private queueOut(q: MockBackend['queues'][number], withTracks = true): Queue {
    const tracks = this.tracksOf(q.trackIds);
    return {
      id: q.id,
      name: q.name,
      slot: q.slot,
      currentIndex: q.currentIndex,
      positionMs: q.positionMs,
      trackCount: tracks.length,
      durationS: tracks.reduce((s, t) => s + t.durationS, 0),
      updatedAt: q.updatedAt,
      ...(withTracks ? { tracks } : {}),
    };
  }
  private plOut(p: MockBackend['playlists'][number], withTracks = true): Playlist {
    const tracks = this.tracksOf(p.trackIds);
    return { id: p.id, name: p.name, createdAt: p.createdAt, updatedAt: p.updatedAt, trackCount: tracks.length, durationS: tracks.reduce((s, t) => s + t.durationS, 0), ...(withTracks ? { tracks } : {}) };
  }
  private getQ(id: string) {
    const q = this.queues.find((x) => x.id === id);
    if (!q) throw new HttpError(404, 'queue not found');
    return q;
  }
  private getP(id: string) {
    const p = this.playlists.find((x) => x.id === id);
    if (!p) throw new HttpError(404, 'playlist not found');
    return p;
  }
  createQueue(name: string, trackIds: string[], startIndex = 0) {
    const used = new Set(this.queues.map((q) => q.slot));
    let slot = 0;
    for (let s = 1; s <= 20; s++)
      if (!used.has(s)) {
        slot = s;
        break;
      }
    if (!slot) {
      const oldest = this.queues.slice().sort((a, b) => (a.updatedAt ?? '').localeCompare(b.updatedAt ?? ''))[0];
      slot = oldest.slot;
      this.queues = this.queues.filter((q) => q !== oldest);
    }
    const q = { id: nid('q'), name, slot, currentIndex: startIndex, positionMs: 0, trackIds, updatedAt: iso() };
    this.queues.push(q);
    return q;
  }

  /** fetch() replacement. */
  fetch = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    await new Promise((r) => setTimeout(r, this.latencyMs));
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://mock');
    const method = (init.method ?? 'GET').toUpperCase();
    const auth = new Headers(init.headers).get('Authorization');
    const token = auth?.startsWith('Bearer ') ? auth.slice(7) : null;
    const body = init.body ? JSON.parse(String(init.body)) : {};
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    try {
      const out = this.route(method, path, url.searchParams, body, token);
      if (out === undefined) return new Response(null, { status: 204 });
      return new Response(JSON.stringify(out), { status: 200, headers: { 'Content-Type': 'application/json' } });
    } catch (e) {
      const he = e instanceof HttpError ? e : new HttpError(500, String(e));
      return new Response(JSON.stringify({ detail: he.detail }), { status: he.status, headers: { 'Content-Type': 'application/json' } });
    }
  };

  private route(method: string, path: string, qs: URLSearchParams, body: Record<string, unknown>, token: string | null): unknown {
    const m = (re: RegExp) => re.exec(path);
    let r: RegExpExecArray | null;
    const L = this.lib;
    // public
    if (path === '/health') return { status: 'ok', serverId: 'mock-server', version: '2.0.0-mock' };
    if (path === '/server-info')
      return { serverId: 'mock-server', name: 'Mock NAS', version: '2.0.0-mock', lanUrl: 'https://192.168.68.2:8443', tunnelUrl: 'https://music.example.com', certSha256: null, registration: 'invite' };
    if (path === '/auth/login' && method === 'POST') {
      const u = this.users.find((x) => x.username.toLowerCase() === String(body.username).toLowerCase());
      if (!u || u.password !== body.password) throw new HttpError(401, 'Wrong username or password');
      if (u.disabled) throw new HttpError(403, 'Account disabled');
      return this.session(u, String(body.deviceName));
    }
    if (path === '/auth/register' && method === 'POST') {
      const inv = this.invites.find((i) => i.code === body.inviteCode && !i.usedBy);
      if (!inv) throw new HttpError(403, 'Invite code is invalid, used or expired');
      if (!/^[A-Za-z0-9_.-]{3,32}$/.test(String(body.username))) throw new HttpError(422, 'Username must be 3–32 characters: letters, digits, _ . -');
      if (String(body.password).length < 8) throw new HttpError(422, 'Password must be at least 8 characters');
      if (this.users.some((u) => u.username.toLowerCase() === String(body.username).toLowerCase())) throw new HttpError(409, 'Username taken');
      const u: MockUser = { id: nid('u'), username: String(body.username), password: String(body.password), isAdmin: false, disabled: false, createdAt: iso() };
      this.users.push(u);
      inv.usedBy = u.id;
      inv.usedAt = iso();
      return this.session(u, String(body.deviceName));
    }
    const who = this.userFor(token);
    if (!who) throw new HttpError(401, 'Not signed in');
    const me = who.user;

    if (path === '/auth/logout') {
      this.tokens.delete(token!);
      return undefined;
    }
    if (path === '/me') return { id: me.id, username: me.username, isAdmin: me.isAdmin, createdAt: me.createdAt };
    if (path === '/me/password') {
      if (body.current !== me.password) throw new HttpError(403, 'Current password is wrong');
      me.password = String(body.new);
      return undefined;
    }
    if (path === '/me/devices') return this.hub.devices().map((d) => ({ id: d.id, name: d.name, kind: d.kind, online: d.online, lastSeen: iso(), current: d.id === who.deviceId }));
    if ((r = m(/^\/me\/devices\/([^/]+)$/)) && method === 'DELETE') return undefined;
    if (path === '/me/pair-code') return { pairCode: 'ABCD2345', qrPayload: 'nasmusic://pair?code=ABCD2345' };
    if (path === '/me/settings') {
      if (method === 'PUT') Object.assign(this.settings, (body.settings as Record<string, unknown>) ?? {});
      return { settings: this.settings };
    }
    if (path === '/me/data') {
      if (method === 'DELETE') return undefined;
      return { events: { count: this.events.length, withContext: this.events.filter((e) => (e as { context?: unknown }).context).length, first: null, last: null }, signals: {} };
    }
    if (path === '/me/data/export') return { events: this.events };

    // library
    if (path === '/library/artists') return L.artists;
    if ((r = m(/^\/library\/artists\/([^/]+)$/))) {
      const a = L.artists.find((x) => x.id === r![1]);
      if (!a) throw new HttpError(404, 'not found');
      return { ...a, albums: L.albums.filter((x) => x.artistId === a.id) };
    }
    if (path === '/library/albums') {
      const sort = qs.get('sort') ?? 'name';
      let out = L.albums.slice();
      if (qs.get('artistId')) out = out.filter((a) => a.artistId === qs.get('artistId'));
      if (sort === 'year') out.sort((a, b) => (b.year ?? 0) - (a.year ?? 0));
      else if (sort === 'added') out.reverse();
      else out.sort((a, b) => a.title.localeCompare(b.title));
      return out;
    }
    if ((r = m(/^\/library\/albums\/([^/]+)$/))) {
      const a = L.albums.find((x) => x.id === r![1]);
      if (!a) throw new HttpError(404, 'not found');
      return { ...a, tracks: L.tracks.filter((t) => t.albumId === a.id).map((t) => this.pub(t)) };
    }
    if ((r = m(/^\/library\/tracks\/([^/]+)\/info$/))) return this.track(decodeURIComponent(r[1]));
    if ((r = m(/^\/library\/tracks\/([^/]+)$/))) return this.pub(this.track(decodeURIComponent(r[1])));
    if (path === '/library/tracks') {
      const out = L.tracks.map((t) => this.pub(t));
      const sort = qs.get('sort');
      if (sort === 'year') out.sort((a, b) => (b.year ?? 0) - (a.year ?? 0));
      else if (sort === 'added') out.reverse();
      else out.sort((a, b) => a.title.localeCompare(b.title));
      return out;
    }
    if (path === '/library/search') {
      const q = (qs.get('q') ?? '').toLowerCase();
      return {
        artists: L.artists.filter((a) => a.name.toLowerCase().includes(q)),
        albums: L.albums.filter((a) => a.title.toLowerCase().includes(q) || a.artist.toLowerCase().includes(q)),
        tracks: L.tracks.filter((t) => t.title.toLowerCase().includes(q)).map((t) => this.pub(t)),
      };
    }
    if (path === '/library/folders') {
      const p = qs.get('path') ?? '';
      const prefix = p ? p + '/' : '';
      const folders = new Map<string, number>();
      const tracks: Track[] = [];
      for (const t of L.tracks) {
        if (t.folder === p) tracks.push(this.pub(t));
        else if (t.folder.startsWith(prefix)) {
          const name = t.folder.slice(prefix.length).split('/')[0];
          folders.set(name, (folders.get(name) ?? 0) + 1);
        }
      }
      return {
        path: p,
        parent: p ? p.split('/').slice(0, -1).join('/') : null,
        folders: [...folders].sort().map(([name, trackCount]) => ({ name, path: prefix + name, trackCount })),
        tracks,
      };
    }
    if (path === '/library/genres') {
      const g = new Map<string, number>();
      for (const t of L.tracks) if (t.genre) g.set(t.genre, (g.get(t.genre) ?? 0) + 1);
      return [...g].sort().map(([name, trackCount]) => ({ name, trackCount }));
    }
    if ((r = m(/^\/library\/genres\/([^/]+)\/tracks$/))) return L.tracks.filter((t) => t.genre === decodeURIComponent(r![1])).map((t) => this.pub(t));
    if ((r = m(/^\/lists\/([a-z_]+)$/))) {
      const all = L.tracks.map((t) => this.pub(t));
      switch (r[1]) {
        case 'favourites':
          return all.filter((t) => t.starred);
        case 'recently_added':
          return all.slice().reverse().slice(0, 50);
        case 'recently_played':
          return all.filter((t) => this.played.has(t.id)).sort((a, b) => this.played.get(b.id)!.last.localeCompare(this.played.get(a.id)!.last));
        case 'most_played':
          return all.filter((t) => this.played.has(t.id)).sort((a, b) => this.played.get(b.id)!.count - this.played.get(a.id)!.count);
        case 'not_played':
          return all.filter((t) => !this.played.has(t.id));
      }
      throw new HttpError(404, 'unknown list');
    }

    // favourites
    if (path === '/favourites') return L.tracks.filter((t) => t.starred).map((t) => this.pub(t));
    if ((r = m(/^\/favourites\/([^/]+)$/))) {
      this.track(r[1]).starred = method === 'PUT';
      return undefined;
    }

    // lyrics
    if ((r = m(/^\/tracks\/([^/]+)\/lyrics$/))) {
      const id = decodeURIComponent(r[1]);
      const t = this.track(id);
      if (method === 'PUT') {
        this.lyricOverrides.set(id, String(body.raw ?? ''));
      } else if (method === 'DELETE') {
        this.lyricOverrides.delete(id);
        return undefined;
      }
      const want = qs.get('source');
      const own = want && want !== 'user' ? undefined : this.lyricOverrides.get(id);
      const fileSrc = hashSource(id) ? 'lrc_file' : 'embedded';
      const raw = own ?? (want === 'user' || (want && want !== fileSrc) ? undefined : L.lyrics.get(id));
      if (raw == null) throw new HttpError(404, `no lyrics for ${t.fileName}`);
      const p = parseLrc(raw);
      return { trackId: id, synced: p.synced, source: own != null ? 'user' : fileSrc, lines: p.lines, text: p.text, canEdit: true };
    }
    if ((r = m(/^\/tracks\/([^/]+)\/why$/))) {
      const t = this.track(r[1]);
      return { reason: `Picked because you often play ${t.artist} in the evening.`, similarTo: L.tracks.filter((x) => x.genre === t.genre && x.id !== t.id).slice(0, 3).map((x) => this.pub(x)) };
    }

    // mixes / discover
    if (path === '/mixes/today') return this.mixes;
    if ((r = m(/^\/mixes\/more-like\/([^/]+)$/))) {
      const t = this.track(r[1]);
      const tracks = L.tracks.filter((x) => x.genre === t.genre).slice(0, 15).map((x) => ({ track: this.pub(x), reason: `Similar to ${t.title}` }));
      const mix = { id: nid('mx'), kind: 'more_like', title: `More like ${t.title}`, createdAt: iso(), tracks, durationS: tracks.reduce((s, x) => s + x.track.durationS, 0) };
      this.mixes.push(mix);
      return mix;
    }
    if ((r = m(/^\/mixes\/([^/]+)$/))) {
      const mix = this.mixes.find((x) => x.id === r![1]);
      if (!mix) throw new HttpError(404, 'not found');
      return mix;
    }
    if (path === '/suggestions') return mockSuggestions;
    if ((r = m(/^\/suggestions\/([^/]+)$/))) {
      const s = mockSuggestions.find((x) => x.id === r![1]);
      if (s) s.status = body.status as typeof s.status;
      return undefined;
    }
    if (path === '/events') {
      const evs = (body.events as { type: string; trackId: string; at: string }[]) ?? [];
      for (const e of evs) {
        this.events.push(e);
        if (e.type === 'play') {
          const p = this.played.get(e.trackId) ?? { count: 0, last: e.at };
          this.played.set(e.trackId, { count: p.count + 1, last: e.at });
        }
      }
      return { accepted: evs.length, duplicates: 0 };
    }

    // playlists
    if (path === '/playlists') {
      if (method === 'POST') {
        const p = { id: nid('p'), name: String(body.name), trackIds: [], createdAt: iso(), updatedAt: iso() };
        this.playlists.push(p);
        return this.plOut(p);
      }
      return this.playlists.map((p) => this.plOut(p, false));
    }
    if ((r = m(/^\/playlists\/([^/]+)$/))) {
      const p = this.getP(r[1]);
      if (method === 'DELETE') {
        this.playlists = this.playlists.filter((x) => x !== p);
        return undefined;
      }
      if (method === 'PATCH') p.name = String(body.name ?? p.name);
      return this.plOut(p);
    }
    if ((r = m(/^\/playlists\/([^/]+)\/tracks$/))) {
      const p = this.getP(r[1]);
      const ids = body.trackIds as string[];
      const pos = typeof body.position === 'number' ? body.position : p.trackIds.length;
      p.trackIds.splice(pos, 0, ...ids);
      p.updatedAt = iso();
      return this.plOut(p);
    }
    if ((r = m(/^\/playlists\/([^/]+)\/tracks\/(\d+)$/))) {
      this.getP(r[1]).trackIds.splice(Number(r[2]), 1);
      return undefined;
    }
    if ((r = m(/^\/playlists\/([^/]+)\/move$/))) {
      const p = this.getP(r[1]);
      const [x] = p.trackIds.splice(Number(body.from), 1);
      p.trackIds.splice(Number(body.to), 0, x);
      return undefined;
    }

    // queues
    if (path === '/queues') {
      if (method === 'POST') {
        const q = this.createQueue(String(body.name), body.trackIds as string[], Number(body.startIndex ?? 0));
        return this.queueOut(q);
      }
      return this.queues.slice().sort((a, b) => a.slot - b.slot).map((q) => this.queueOut(q, false));
    }
    if ((r = m(/^\/queues\/([^/]+)$/))) {
      const q = this.getQ(r[1]);
      if (method === 'DELETE') {
        this.queues = this.queues.filter((x) => x !== q);
        return undefined;
      }
      if (method === 'PATCH') {
        if (body.name !== undefined) q.name = String(body.name);
        if (body.currentIndex !== undefined) q.currentIndex = Number(body.currentIndex);
        if (body.positionMs !== undefined) q.positionMs = Number(body.positionMs);
        q.updatedAt = iso();
      }
      return this.queueOut(q);
    }
    if ((r = m(/^\/queues\/([^/]+)\/tracks$/))) {
      const q = this.getQ(r[1]);
      const ids = body.trackIds as string[];
      const pos = body.position === 'next' ? q.currentIndex + 1 : body.position === 'end' || body.position == null ? q.trackIds.length : Number(body.position);
      q.trackIds.splice(pos, 0, ...ids);
      if (q.trackIds.length > ids.length && pos <= q.currentIndex) q.currentIndex += ids.length;
      q.updatedAt = iso();
      return this.queueOut(q);
    }
    if ((r = m(/^\/queues\/([^/]+)\/tracks\/(\d+)$/))) {
      const q = this.getQ(r[1]);
      const pos = Number(r[2]);
      q.trackIds.splice(pos, 1);
      if (pos < q.currentIndex) q.currentIndex--;
      return undefined;
    }
    if ((r = m(/^\/queues\/([^/]+)\/move$/))) {
      const q = this.getQ(r[1]);
      const [x] = q.trackIds.splice(Number(body.from), 1);
      q.trackIds.splice(Number(body.to), 0, x);
      return undefined;
    }
    if ((r = m(/^\/queues\/([^/]+)\/save-as-playlist$/))) {
      const q = this.getQ(r[1]);
      const p = { id: nid('p'), name: String(body.name), trackIds: q.trackIds.slice(), createdAt: iso(), updatedAt: iso() };
      this.playlists.push(p);
      return this.plOut(p);
    }

    // admin
    if (path.startsWith('/admin/') && !me.isAdmin) throw new HttpError(403, 'Admins only');
    if (path === '/admin/invites') {
      if (method === 'POST') {
        const code = Math.random().toString(36).slice(2, 10).toUpperCase();
        const inv: Invite = { code, note: (body.note as string) ?? null, createdAt: iso(), expiresAt: new Date(Date.now() + Number(body.expiresInDays ?? 7) * 864e5).toISOString(), url: `${location.origin}/join/${code}`, createdBy: me.username };
        this.invites.push(inv);
        return inv;
      }
      return this.invites;
    }
    if ((r = m(/^\/admin\/invites\/([^/]+)$/))) {
      this.invites = this.invites.filter((i) => i.code !== r![1]);
      return undefined;
    }
    if (path === '/admin/users') return this.users.map(({ password: _p, ...u }) => u);
    if ((r = m(/^\/admin\/users\/([^/]+)$/))) {
      const u = this.users.find((x) => x.id === r![1]);
      if (!u) throw new HttpError(404, 'not found');
      if (body.isAdmin === false && u.isAdmin && this.users.filter((x) => x.isAdmin).length === 1) throw new HttpError(409, "Can't remove the last admin");
      if (body.disabled !== undefined) u.disabled = !!body.disabled;
      if (body.isAdmin !== undefined) u.isAdmin = !!body.isAdmin;
      const { password: _p, ...out } = u;
      return out;
    }
    if ((r = m(/^\/admin\/users\/([^/]+)\/reset-password$/))) {
      const u = this.users.find((x) => x.id === r![1]);
      if (u) u.password = String(body.new);
      return undefined;
    }
    throw new HttpError(404, `mock: no route ${method} ${path}`);
  }

  // ---- media ----
  private streams = new Map<string, string>();
  media: MediaResolver = {
    art: async (albumId) => {
      const a = this.lib.albums.find((x) => x.id === albumId);
      return artSvg(albumId, a?.title ?? '?');
    },
    stream: async (trackId) => {
      let u = this.streams.get(trackId);
      if (!u) {
        const t = this.track(trackId);
        u = URL.createObjectURL(toneWav(trackId, t.durationS));
        this.streams.set(trackId, u);
      }
      return u;
    },
    suggestionCover: async (id) => artSvg(id, mockSuggestions.find((s) => s.id === id)?.album ?? '?'),
  };

  /** WebSocket replacement for Connect. */
  socket = (url: string): SocketLike => {
    const token = new URL(url, 'ws://mock').searchParams.get('token');
    return this.hub.connect(token);
  };
}

function hashSource(id: string) {
  return id.endsWith('3');
}

// ---- Connect hub with a simulated phone ----

class MockSocket implements SocketLike {
  readyState = 0;
  onopen: SocketLike['onopen'] = null;
  onmessage: SocketLike['onmessage'] = null;
  onclose: SocketLike['onclose'] = null;
  onerror: SocketLike['onerror'] = null;
  deviceId: string | null = null;
  constructor(
    private hub: MockHub,
    public token: string | null,
  ) {
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.({});
    }, 30);
  }
  send(data: string) {
    setTimeout(() => this.hub.receive(this, JSON.parse(data)), 10);
  }
  deliver(msg: unknown) {
    if (this.readyState === 1) setTimeout(() => this.onmessage?.({ data: JSON.stringify(msg) }), 10);
  }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.hub.disconnect(this);
    setTimeout(() => this.onclose?.({}), 0);
  }
}

const PHONE = 'mock-phone';

export class MockHub {
  private sockets = new Set<MockSocket>();
  private known = new Map<string, { name: string; kind: ConnectDevice['kind'] }>([[PHONE, { name: 'Pixel 8 (mock)', kind: 'android' }]]);
  state: PlaybackState = {
    activeDeviceId: PHONE,
    queueId: 'q1',
    index: 0,
    trackId: null,
    positionMs: 42000,
    positionAt: iso(),
    playing: true,
    volume: 0.8,
    shuffle: false,
    repeat: 'off',
    speed: 1,
    rev: 1,
  };
  private phoneTimer: ReturnType<typeof setInterval> | null = null;

  constructor(private backend: MockBackend) {
    setTimeout(() => {
      const q = backend.queues.find((x) => x.id === 'q1');
      this.state.trackId = q?.trackIds[0] ?? null;
      this.startPhone();
    }, 0);
  }

  registerDevice(id: string, name: string) {
    this.known.set(id, { name, kind: 'web' });
  }

  connect(token: string | null): MockSocket {
    const s = new MockSocket(this, token);
    this.sockets.add(s);
    return s;
  }

  devices(): ConnectDevice[] {
    const online = new Set([...this.sockets].map((s) => s.deviceId).filter(Boolean));
    online.add(PHONE);
    return [...this.known].map(([id, d]) => ({ id, name: d.name, kind: d.kind, canPlay: true, online: online.has(id), isActive: this.state.activeDeviceId === id }));
  }

  private broadcast(msg: unknown) {
    for (const s of this.sockets) if (s.deviceId) s.deliver(msg);
  }
  private socketOf(id: string) {
    return [...this.sockets].find((s) => s.deviceId === id);
  }
  private setState(p: Partial<PlaybackState>) {
    this.state = { ...this.state, ...p, rev: this.state.rev + 1 };
    this.broadcast({ type: 'state', ...this.state });
  }
  private pos() {
    return this.state.playing ? this.state.positionMs + (Date.now() - Date.parse(this.state.positionAt)) * this.state.speed : this.state.positionMs;
  }

  receive(s: MockSocket, msg: ClientMessage) {
    switch (msg.type) {
      case 'hello': {
        const who = this.backend.userFor(s.token);
        if (!who) return s.close();
        s.deviceId = who.deviceId;
        this.known.set(who.deviceId, { name: msg.deviceName, kind: msg.kind });
        s.deliver({ type: 'welcome', deviceId: who.deviceId, devices: this.devices(), state: { ...this.state, positionMs: this.pos(), positionAt: iso() } });
        this.broadcast({ type: 'devices', devices: this.devices() });
        break;
      }
      case 'ping':
        s.deliver({ type: 'pong' });
        break;
      case 'state': {
        if (s.deviceId !== this.state.activeDeviceId) return;
        const { type: _t, rev: _r, ...rest } = msg;
        this.setState({ ...rest, activeDeviceId: s.deviceId });
        break;
      }
      case 'transfer':
        this.transfer(msg.toDeviceId, msg.play);
        break;
      case 'command': {
        const target = msg.targetDeviceId ?? this.state.activeDeviceId;
        if (!target) {
          if (msg.action === 'play' && s.deviceId) this.transfer(s.deviceId, true);
          return;
        }
        if (target === PHONE) this.phoneCommand(msg.action, msg.args ?? {});
        else this.socketOf(target)?.deliver({ type: 'command', action: msg.action, args: msg.args ?? {}, fromDeviceId: s.deviceId });
        break;
      }
    }
  }

  private transfer(to: string, play: boolean) {
    const prev = this.state.activeDeviceId;
    const positionMs = this.pos();
    if (prev && prev !== to) {
      if (prev === PHONE) this.stopPhone();
      else this.socketOf(prev)?.deliver({ type: 'release' });
    }
    this.state = { ...this.state, activeDeviceId: to, positionMs, positionAt: iso(), playing: play, rev: this.state.rev + 1 };
    if (to === PHONE) this.startPhone();
    else this.socketOf(to)?.deliver({ type: 'takeover', state: this.state });
    this.broadcast({ type: 'state', ...this.state });
    this.broadcast({ type: 'devices', devices: this.devices() });
  }

  disconnect(s: MockSocket) {
    this.sockets.delete(s);
    this.broadcast({ type: 'devices', devices: this.devices() });
    if (s.deviceId && s.deviceId === this.state.activeDeviceId)
      setTimeout(() => {
        if (!this.socketOf(s.deviceId!) && this.state.activeDeviceId === s.deviceId) this.setState({ playing: false, positionMs: this.state.positionMs });
      }, 15000);
  }

  // The simulated phone plays the queue by the clock.
  private startPhone() {
    this.stopPhone();
    this.phoneTimer = setInterval(() => {
      if (this.state.activeDeviceId !== PHONE || !this.state.playing) return;
      const q = this.backend.queues.find((x) => x.id === this.state.queueId);
      const t = this.backend.lib.tracks.find((x) => x.id === this.state.trackId);
      if (q && t && this.pos() >= t.durationS * 1000) this.phoneCommand('next', {});
      else this.setState({ positionMs: this.pos(), positionAt: iso() });
    }, 5000);
  }
  private stopPhone() {
    if (this.phoneTimer) clearInterval(this.phoneTimer);
    this.phoneTimer = null;
  }

  private phoneCommand(action: string, args: Record<string, unknown>) {
    const q = this.backend.queues.find((x) => x.id === this.state.queueId);
    const n = q?.trackIds.length ?? 0;
    const go = (i: number) => this.setState({ index: i, trackId: q?.trackIds[i] ?? null, positionMs: 0, positionAt: iso() });
    switch (action) {
      case 'play':
        return this.setState({ playing: true, positionMs: this.pos(), positionAt: iso() });
      case 'pause':
        return this.setState({ playing: false, positionMs: this.pos(), positionAt: iso() });
      case 'next':
        return n ? go((this.state.index + 1) % n) : undefined;
      case 'prev':
        return n ? go(Math.max(0, this.state.index - 1)) : undefined;
      case 'seek':
        return this.setState({ positionMs: Number(args.positionMs ?? 0), positionAt: iso() });
      case 'setVolume':
        return this.setState({ volume: Number(args.volume) });
      case 'setShuffle':
        return this.setState({ shuffle: !!args.on });
      case 'setRepeat':
        return this.setState({ repeat: args.mode as PlaybackState['repeat'] });
      case 'setSpeed':
        return this.setState({ speed: Number(args.speed) });
      case 'playQueue': {
        const qq = this.backend.queues.find((x) => x.id === args.queueId);
        const i = Number(args.index ?? 0);
        return this.setState({ queueId: String(args.queueId), index: i, trackId: qq?.trackIds[i] ?? null, positionMs: 0, positionAt: iso(), playing: true });
      }
      case 'playTracks': {
        const nq = this.backend.createQueue(String(args.queueName ?? 'Queue'), args.trackIds as string[], Number(args.index ?? 0));
        this.broadcast({ type: 'queues', queueId: nq.id });
        return this.setState({ queueId: nq.id, index: nq.currentIndex, trackId: nq.trackIds[nq.currentIndex], positionMs: 0, positionAt: iso(), playing: true });
      }
    }
  }
}

export const mockBackend = (() => {
  let b: MockBackend | null = null;
  return () => (b ??= new MockBackend());
})();
