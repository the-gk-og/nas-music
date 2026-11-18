// In-browser mock of the v2 server (HTTP + Connect hub), for `npm run dev:mock` and the server address "mock".
// Login: admin / admin123 (admin) — or register with invite code WELCOME1.
import type { SocketLike } from '../connect';
import { parseLrc } from '../lyrics';
import type {
  CatalogItem,
  ClientMessage,
  Pin,
  Shelf,
  ShelfItem,
  ToGetItem,
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
  pinsList: Pin[] = [];
  toget: ToGetItem[] = [];
  catalog: CatalogItem[] = [];
  radioSeen = new Map<string, Set<string>>();

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
    this.pinsList.push({ type: 'playlist', id: 'p1', title: 'car', subtitle: 'Playlist', albumId: 'al0', pinnedAt: iso() });
    this.catalog = [
      ...['Harbor Lights|Undertow|2021', 'Moss & Mortar|Field Notes|2019', 'The Paper Kites|Lantern Days|2018', 'Sable Coast|Night Swimming|2023'].map((x, i) => {
        const [artist, title, year] = x.split('|');
        return { id: 'c' + i, type: 'album' as const, title, artist, year: Number(year), hasCover: true, hasPreview: true, owned: false };
      }),
      ...['Undertow|Harbor Lights', 'Glass Rivers|Sable Coast', 'Conversations on a Train|Moss & Mortar'].map((x, i) => {
        const [title, artist] = x.split('|');
        return { id: 'ct' + i, type: 'track' as const, title, artist, album: null, hasCover: true, hasPreview: true, owned: false };
      }),
      // An owned match, to show the "In your library" marking.
      { id: 'c9', type: 'album' as const, title: t[0].album, artist: t[0].artist, year: t[0].year, hasCover: true, hasPreview: true, owned: true, ownedId: t[0].albumId },
    ];
    this.toget.push({ id: 'tg1', type: 'album', title: 'Field Notes', artist: 'Moss & Mortar', year: 2019, catalogId: 'c1', hasCover: true, status: 'want', note: null, links: storeLinks('Moss & Mortar', 'Field Notes'), createdAt: iso() });
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
      radio: this.radioSeen?.has(q.id) || undefined,
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

    // ---- v3 ----
    if (path === '/home') return { shelves: this.homeShelves(who.deviceId) };
    if (path === '/foryou') return { shelves: this.forYouShelves() };
    if (path === '/search') return this.search(qs.get('q') ?? '');
    if ((r = m(/^\/catalog\/([^/]+)\/(cover|preview)$/))) throw new HttpError(404, 'mock media is served by the media resolver');
    if (path === '/toget') {
      if (method === 'POST') {
        const b = body as { catalogId?: string; type: 'album' | 'track'; title: string; artist: string; year?: number; note?: string };
        const existing = this.toget.find((x) => b.catalogId && x.catalogId === b.catalogId);
        if (existing) {
          existing.status = 'want';
          return existing;
        }
        const item: ToGetItem = { id: nid('tg'), type: b.type, title: b.title, artist: b.artist, year: b.year ?? null, catalogId: b.catalogId ?? null, hasCover: !!b.catalogId, status: 'want', note: b.note ?? null, links: storeLinks(b.artist, b.title), createdAt: iso() };
        this.toget.unshift(item);
        return item;
      }
      return this.toget;
    }
    if ((r = m(/^\/toget\/([^/]+)$/))) {
      const item = this.toget.find((x) => x.id === r![1]);
      if (!item) throw new HttpError(404, 'not found');
      if (method === 'DELETE') {
        this.toget = this.toget.filter((x) => x !== item);
        return undefined;
      }
      if (body.status) item.status = body.status as ToGetItem['status'];
      if ('note' in body) item.note = (body.note as string) ?? null;
      return item;
    }
    if (path === '/pins') {
      if (method === 'POST') {
        const type = body.type as Pin['type'];
        const id = String(body.id);
        if (!this.pinsList.some((p) => p.type === type && p.id === id)) this.pinsList.unshift({ ...this.describe(type, id), pinnedAt: iso() });
        return this.pinsList.find((p) => p.type === type && p.id === id);
      }
      return this.pinsList;
    }
    if ((r = m(/^\/pins\/([a-z]+)\/([^/]+)$/)) && method === 'DELETE') {
      this.pinsList = this.pinsList.filter((p) => !(p.type === r![1] && p.id === decodeURIComponent(r![2])));
      return undefined;
    }
    if (path === '/radio' && method === 'POST') {
      const seed = (body.seed ?? { type: 'random' }) as { type: string; id?: string };
      const name = seed.type === 'random' ? 'Random radio' : `${this.describe(seed.type as Pin['type'], seed.id ?? '').title} radio`;
      const picks = this.radioPicks(seed, new Set(), 25);
      const q = this.createQueue(name, picks, 0);
      this.radioSeen.set(q.id, new Set(picks));
      return { ...this.queueOut(q), radio: true };
    }
    if ((r = m(/^\/radio\/([^/]+)\/more$/))) {
      const q = this.getQ(r[1]);
      const seen = this.radioSeen.get(q.id) ?? new Set(q.trackIds);
      const more = this.radioPicks({ type: 'track', id: q.trackIds.at(-1) }, seen, 15);
      more.forEach((id) => seen.add(id));
      q.trackIds.push(...more);
      q.updatedAt = iso();
      return { ...this.queueOut(q), radio: true };
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

  private item(type: ShelfItem['type'], id: string): ShelfItem {
    const d = this.describe(type as Pin['type'], id);
    return { type, id, title: d.title, subtitle: d.subtitle, albumId: d.albumId };
  }

  describe(type: Pin['type'] | string, id: string): Pin {
    const L = this.lib;
    if (type === 'album') {
      const a = L.albums.find((x) => x.id === id);
      return { type: 'album', id, title: a?.title ?? id, subtitle: a ? `Album · ${a.artist}` : 'Album', albumId: id };
    }
    if (type === 'artist') {
      const a = L.artists.find((x) => x.id === id);
      return { type: 'artist', id, title: a?.name ?? id, subtitle: 'Artist', albumId: L.albums.find((x) => x.artistId === id)?.id ?? null };
    }
    if (type === 'playlist') {
      const p = this.playlists.find((x) => x.id === id);
      return { type: 'playlist', id, title: p?.name ?? id, subtitle: 'Playlist', albumId: this.lib.tracks.find((t) => t.id === p?.trackIds[0])?.albumId ?? null };
    }
    if (type === 'mix') {
      const m = this.mixes.find((x) => x.id === id);
      return { type: 'mix', id, title: m?.title ?? id, subtitle: 'Mix', albumId: m?.tracks[0]?.track.albumId ?? null };
    }
    const t = this.lib.tracks.find((x) => x.id === id);
    return { type: 'album', id, title: t?.title ?? id, subtitle: t?.artist ?? null, albumId: t?.albumId ?? null };
  }

  private radioPicks(seed: { type: string; id?: string }, seen: Set<string>, n: number): string[] {
    const L = this.lib.tracks;
    let genre: string | null | undefined = null;
    if (seed.type === 'track') genre = L.find((t) => t.id === seed.id)?.genre;
    else if (seed.type === 'album') genre = L.find((t) => t.albumId === seed.id)?.genre;
    else if (seed.type === 'artist') genre = L.find((t) => t.artistId === seed.id)?.genre;
    const pool = L.filter((t) => !seen.has(t.id));
    const near = pool.filter((t) => !genre || t.genre === genre);
    const rest = pool.filter((t) => !near.includes(t));
    const shuffled = [...near.sort(() => Math.random() - 0.5), ...rest.sort(() => Math.random() - 0.5)];
    return shuffled.slice(0, n).map((t) => t.id);
  }

  private homeShelves(_deviceId: string): Shelf[] {
    const L = this.lib;
    const recent: ShelfItem[] = [
      this.item('album', L.albums[0].id),
      { type: 'mix', id: this.mixes[0].id, title: this.mixes[0].title, subtitle: 'Mix', artIds: this.mixes[0].tracks.slice(0, 4).map((x) => x.track.albumId) },
      this.item('playlist', 'p1'),
      this.item('album', L.albums[3].id),
      this.item('album', L.albums[5].id),
      { type: 'radio', id: 'artist:ar2', title: `${L.artists[2].name} radio`, subtitle: 'Radio', albumId: L.albums[4].id },
      this.item('playlist', 'p2'),
      this.item('album', L.albums[8].id),
    ];
    const cont: ShelfItem[] = [
      { ...this.item('album', L.albums[2].id), progress: 0.4 },
      ...this.queues.filter((q) => q.currentIndex > 0 || q.positionMs > 0).map((q) => ({ type: 'queue' as const, id: q.id, title: q.name, subtitle: 'Queue', albumId: L.tracks.find((t) => t.id === q.trackIds[q.currentIndex])?.albumId, progress: q.trackIds.length ? q.currentIndex / q.trackIds.length : 0 })),
    ];
    return [
      { id: 'radio', title: 'Start radio', items: [] },
      { id: 'devices', title: 'Your devices', items: [] },
      { id: 'recent', title: 'Recently played', items: recent },
      { id: 'continue', title: 'Continue listening', items: cont },
      { id: 'pinned', title: 'Pinned', items: this.pinsList.map((p) => ({ type: p.type, id: p.id, title: p.title, subtitle: p.subtitle, albumId: p.albumId })) },
    ];
  }

  private forYouShelves(): Shelf[] {
    const L = this.lib;
    const seed = L.tracks[4];
    const similar = L.tracks.filter((t) => t.genre === seed.genre && t.id !== seed.id).slice(0, 10);
    return [
      { id: 'mixes', title: 'Your mixes', items: this.mixes.map((m) => ({ type: 'mix' as const, id: m.id, title: m.title, subtitle: `${m.tracks.length} songs`, artIds: m.tracks.slice(0, 4).map((x) => x.track.albumId) })) },
      {
        id: 'radios',
        title: 'Radio',
        items: [
          { type: 'radio', id: `track:${seed.id}`, title: `${seed.title} radio`, subtitle: 'Song radio', albumId: seed.albumId },
          ...L.artists.slice(0, 5).map((a) => ({ type: 'radio' as const, id: `artist:${a.id}`, title: `${a.name} radio`, subtitle: 'Artist radio', albumId: L.albums.find((x) => x.artistId === a.id)?.id })),
        ],
      },
      { id: `because:${seed.id}`, kind: 'because', title: `Because you played ${seed.title}`, items: similar.map((t) => ({ type: 'track' as const, id: t.id, title: t.title, subtitle: t.artist, albumId: t.albumId, track: this.pubTrack(t) })) },
      { id: 'suggestions', title: 'Suggestions', items: mockSuggestions.filter((x) => x.status === 'new').map((x) => ({ type: 'catalog' as const, id: x.id, title: x.album, subtitle: x.artist, catalog: { id: x.id, type: 'album' as const, title: x.album, artist: x.artist, year: x.year, hasCover: true, hasPreview: true, owned: false } })) },
      { id: 'toget', title: 'To get', items: this.toget.filter((x) => x.status === 'want').map((x) => ({ type: 'catalog' as const, id: x.id, title: x.title, subtitle: x.artist, catalog: { id: x.catalogId ?? x.id, type: x.type, title: x.title, artist: x.artist, hasCover: x.hasCover, hasPreview: !!x.catalogId, owned: false, inToGet: true } })) },
    ];
  }

  pubTrack(t: TrackInfo): Track {
    return this.pub(t);
  }

  private search(q: string) {
    const term = q.toLowerCase().trim();
    const L = this.lib;
    const tracks = L.tracks.filter((t) => t.title.toLowerCase().includes(term) || t.artist.toLowerCase().includes(term)).slice(0, 20).map((t) => this.pub(t));
    const albums = L.albums.filter((a) => a.title.toLowerCase().includes(term) || a.artist.toLowerCase().includes(term));
    const artists = L.artists.filter((a) => a.name.toLowerCase().includes(term));
    const playlists = this.playlists.filter((p) => p.name.toLowerCase().includes(term)).map((p) => this.plOut(p, false));
    const queues = this.queues.filter((x) => x.name.toLowerCase().includes(term)).map((x) => this.queueOut(x, false));
    const getIds = new Set(this.toget.filter((x) => x.status === 'want').map((x) => x.catalogId));
    const catalog = this.catalog
      .filter((c) => c.title.toLowerCase().includes(term) || c.artist.toLowerCase().includes(term))
      .map((c) => ({ ...c, inToGet: getIds.has(c.id) }));
    const top: ShelfItem | null = artists[0]
      ? { type: 'artist', id: artists[0].id, title: artists[0].name, subtitle: 'Artist', albumId: L.albums.find((a) => a.artistId === artists[0].id)?.id }
      : albums[0]
        ? { type: 'album', id: albums[0].id, title: albums[0].title, subtitle: `Album · ${albums[0].artist}`, albumId: albums[0].id }
        : tracks[0]
          ? { type: 'track', id: tracks[0].id, title: tracks[0].title, subtitle: tracks[0].artist, albumId: tracks[0].albumId, track: tracks[0] }
          : null;
    return { top, tracks, albums, artists, playlists, queues, catalog };
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
    catalogCover: async (id) => artSvg(id, this.catalog.find((c) => c.id === id)?.title ?? mockSuggestions.find((s) => s.id === id)?.album ?? '?'),
    catalogPreview: async (id) => URL.createObjectURL(toneWav(id, 30)),
  };

  /** WebSocket replacement for Connect. */
  socket = (url: string): SocketLike => {
    const token = new URL(url, 'ws://mock').searchParams.get('token');
    return this.hub.connect(token);
  };
}

function storeLinks(artist: string, title: string) {
  const q = encodeURIComponent(`${artist} ${title}`);
  return [
    { name: 'Bandcamp', url: `https://bandcamp.com/search?q=${q}` },
    { name: 'Qobuz', url: `https://www.qobuz.com/search?q=${q}` },
  ];
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
        else if (msg.action === 'playTracks') {
          // Like the real server: create the queue, deliver playQueue to the active device.
          const a = msg.args ?? {};
          const nq = this.backend.createQueue(String(a.queueName ?? 'Now playing'), a.trackIds as string[], Number(a.index ?? 0));
          this.broadcast({ type: 'queues', queueId: nq.id });
          this.socketOf(target)?.deliver({ type: 'command', action: 'playQueue', args: { queueId: nq.id, index: nq.currentIndex }, fromDeviceId: s.deviceId });
        } else this.socketOf(target)?.deliver({ type: 'command', action: msg.action, args: msg.args ?? {}, fromDeviceId: s.deviceId });
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
