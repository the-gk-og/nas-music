// In-browser mock of the v2 server (HTTP + Connect hub), for `npm run dev:mock` and the server address "mock".
// Login: admin / admin123 (admin) — or register with invite code WELCOME1.
import type { SocketLike } from '../connect';
import { parseLrc } from '../lyrics';
import type {
  Blend,
  RadioSeed,
  RadioStation,
  VibeConstraint,
  CatalogItem,
  ReleaseRadar,
  ServerWrapped,
  Wrapped,
  ClientMessage,
  PinType,
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
  SmartDefinition,
  SmartPlaylist,
} from '../types';
import type { MediaResolver } from './client';
import { artSvg, buildLibrary, mockMixes, mockSuggestions, toneWav } from './mockData';
import { AdminError, MockAdmin } from './mockAdmin';

interface MockUser {
  id: string;
  username: string;
  password: string;
  isAdmin: boolean;
  disabled: boolean;
  createdAt: string;
}

const iso = (ms = Date.now()) => new Date(ms).toISOString();
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
  admin = new MockAdmin();
  notes: { id: string; kind: string; title: string; body: string; data: Record<string, unknown>; createdAt: string; read: boolean }[] = [
    { id: 'n1', kind: 'new_music', title: '12 new songs', body: '2 new albums from AJR', data: { tracks: 12, albums: 2 }, createdAt: new Date().toISOString(), read: false },
  ];
  scrobbles: Record<string, { connected: boolean; enabled: boolean; nowPlaying: boolean; username: string | null }> = {
    listenbrainz: { connected: false, enabled: false, nowPlaying: true, username: null },
    lastfm: { connected: false, enabled: false, nowPlaying: true, username: null },
  };
  private importMock(path: string, method: string) {
    const status = { imported: { listenbrainz: 0, lastfm: 0, spotify: 0 }, lastfm: null, lastfmAvailable: true, lastfmUsername: null };
    const hist = { scanned: 120, matched: 80, imported: 78, alreadyKnown: 2, unmatched: 40, tooShort: 12, topMissing: [{ artist: 'Somebody', title: 'Elsewhere', plays: 9 }], toGetAdded: 0 };
    const playlist = { name: 'Imported', total: 3, matched: 2, missing: 1, playlistId: 'p1', unchanged: false, toGetAdded: 1, missingSongs: [{ artist: 'Somebody', title: 'Elsewhere' }] };
    if (path === '/me/import') return status;
    if (path === '/me/import/spotify') return { files: ['Streaming_History_Audio_2024.json'], playlists: [], history: hist, toGetAdded: 0 };
    if (path === '/me/import/lastfm/csv') return hist;
    if (path === '/me/import/lastfm') return { ...status, lastfm: { state: 'done', username: 'me', ...hist } };
    if (path === '/me/import/m3u') return playlist;
    if (path.startsWith('/me/import/history/') && method === 'DELETE') return { removed: 0, ...status };
    throw new HttpError(404, 'not found');
  }

  private scrobbleMock(path: string, method: string, body: Record<string, unknown>) {
    const parts = path.split('/'); // ['', 'me', 'scrobble', service?, action?]
    const svc = parts[3];
    const s = svc ? this.scrobbles[svc] : undefined;
    if (svc === 'lastfm' && parts[4] === 'connect') return { authUrl: 'https://www.last.fm/api/auth/?api_key=demo&token=demo' };
    if (s && parts[4] === 'complete') Object.assign(s, { connected: true, enabled: true, username: 'demo' });
    if (s && parts[4] === 'connect') Object.assign(s, { connected: true, enabled: true, username: 'demo' });
    if (s && !parts[4] && method === 'DELETE') Object.assign(s, { connected: false, enabled: false, username: null });
    if (s && !parts[4] && method === 'PUT') {
      if (typeof body.enabled === 'boolean') s.enabled = body.enabled;
      if (typeof body.nowPlaying === 'boolean') s.nowPlaying = body.nowPlaying;
    }
    return {
      services: Object.entries(this.scrobbles).map(([service, v]) => ({
        service, available: true, ...v, needsReauth: false, lastScrobbleAt: v.connected ? iso() : null, pending: 0, failed: 0, sent: v.connected ? 42 : 0,
      })),
      import: null,
      importedEvents: 0,
      ...(parts[4] === 'import' && method === 'DELETE' ? { removed: 0 } : {}),
    };
  }
  adminWants(method: string, path: string, qs: URLSearchParams, body: Record<string, unknown>, me: { id: string; username: string }) {
    const all = () =>
      [...this.toget.map((x) => ({ ...x, userId: me.id, username: me.username })), ...this.otherWants].map((x) => {
        const a = this.wantMarks.get(x.id);
        return { ...x, done: !!a, doneAt: a?.at ?? null, doneNote: a?.note ?? null, doneBy: a?.by ?? null, doneAuto: !!a?.auto, suggestDone: !a && (!!x.ownedId || x.status === 'got') };
      });
    const one = (id: string) => {
      const it = all().find((x) => x.id === id);
      if (!it) throw new HttpError(404, 'no such item');
      return it;
    };
    if (path === '/admin/wants/check-found') {
      const ids = body.ids as string[] | undefined;
      const found = all().filter((x) => x.suggestDone && (!ids || ids.includes(x.id)));
      for (const x of found) this.wantMarks.set(x.id, { at: iso(), note: 'Found in the library', by: me.username, auto: true });
      return { checked: found.length };
    }
    const r = path.match(/^\/admin\/wants\/([^/]+)\/done$/);
    if (r) {
      one(r[1]);
      if (method === 'DELETE') this.wantMarks.delete(r[1]);
      else {
        const note = String(body.note ?? '').trim() || null;
        this.wantMarks.set(r[1], { at: this.wantMarks.get(r[1])?.at ?? iso(), note, by: me.username, auto: false });
      }
      return one(r[1]);
    }
    const items = all();
    const users = [...new Map(items.map((x) => [x.userId, x.username])).entries()].map(([userId, username]) => {
      const mine = items.filter((x) => x.userId === userId);
      return { userId, username, open: mine.filter((x) => !x.done && x.status !== 'dismissed').length, done: mine.filter((x) => x.done).length, found: mine.filter((x) => x.suggestDone).length, total: mine.length };
    });
    const st = qs.get('status') ?? 'active';
    const done = qs.get('done');
    const q = (qs.get('q') ?? '').trim().toLowerCase();
    return {
      items: items.filter(
        (x) =>
          (st === 'all' || (st === 'active' ? x.status !== 'dismissed' : x.status === st)) &&
          (!qs.get('userId') || x.userId === qs.get('userId')) &&
          (!qs.get('type') || x.type === qs.get('type')) &&
          (done === null || String(x.done) === done) &&
          (!q || [x.title, x.artist, x.note ?? '', x.username].some((v) => v.toLowerCase().includes(q))),
      ),
      users,
    };
  }
  family = { enabled: false, hideProbable: true, hideUnknown: false, locked: false, hasPin: false };
  voiceTokens: { id: string; name: string; createdAt: string; lastUsed: string | null }[] = [];
  voicePrefs = new Map<string, { aliases: string[]; isDefault: boolean }>();
  pinsList: { type: PinType; id: string }[] = [];
  toget: ToGetItem[] = [];
  // schema v8 admin want list: other users' items + the admin's check-offs
  otherWants: (ToGetItem & { userId: string; username: string })[] = [
    { id: 'tgo1', userId: 'u-sam', username: 'sam', type: 'album', title: 'Paper Lanterns', artist: 'Harbour Lights', year: 2024, status: 'want', source: 'search', note: 'the deluxe one', links: storeLinks('Harbour Lights', 'Paper Lanterns'), createdAt: iso(Date.now() - 3 * 864e5) },
    { id: 'tgo2', userId: 'u-sam', username: 'sam', type: 'track', title: 'Night Bus', artist: 'Low Tide', year: 2021, status: 'got', ownedId: 't1_1', source: 'manual', note: null, links: storeLinks('Low Tide', 'Night Bus'), createdAt: iso(Date.now() - 9 * 864e5) },
    { id: 'tgo3', userId: 'u-kid', username: 'kiddo', type: 'album', title: 'Bright Side', artist: 'The Kites', year: 2025, status: 'want', source: 'suggestion', note: null, links: storeLinks('The Kites', 'Bright Side'), createdAt: iso(Date.now() - 864e5) },
  ];
  wantMarks = new Map<string, { at: string; note: string | null; by: string; auto: boolean }>([['tgo1', { at: iso(Date.now() - 864e5), note: 'Ripped from CD', by: 'admin', auto: false }]]);
  catalog: CatalogItem[] = [];
  radioSeen = new Map<string, Set<string>>();
  radioSeeds = new Map<string, RadioSeed>();
  radioSteering = new Map<string, RadioStation['steering']>();
  smart: SmartPlaylist[] = [];

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
    this.pinsList.push({ type: 'playlist', id: 'p1' });
    this.catalog = [
      ...['Harbor Lights|Undertow|2021', 'Moss & Mortar|Field Notes|2019', 'The Paper Kites|Lantern Days|2018', 'Sable Coast|Night Swimming|2023'].map((x, i) => {
        const [artist, title, year] = x.split('|');
        const id = `dz:album:${100 + i}`;
        return { id, type: 'album' as const, title, artist, year: Number(year), artUrl: `catalog/${id}/cover`, hasPreview: true, owned: false, links: storeLinks(artist, title) };
      }),
      ...['Undertow|Harbor Lights', 'Glass Rivers|Sable Coast', 'Conversations on a Train|Moss & Mortar'].map((x, i) => {
        const [title, artist] = x.split('|');
        const id = `dz:track:${200 + i}`;
        return { id, type: 'track' as const, title, artist, album: null, artUrl: `catalog/${id}/cover`, hasPreview: true, owned: false, links: storeLinks(artist, title) };
      }),
      // An owned match, to show the "In your library" marking.
      { id: 'dz:album:999', type: 'album' as const, title: t[0].album, artist: t[0].artist, year: t[0].year, artUrl: `art/${t[0].albumId}`, hasPreview: true, owned: true, ownedId: t[0].albumId, links: [] },
    ];
    this.toget.push({ id: 'tg1', type: 'album', title: 'Field Notes', artist: 'Moss & Mortar', year: 2019, catalogId: 'dz:album:101', artUrl: 'catalog/dz:album:101/cover', hasPreview: true, status: 'want', source: 'search', note: null, links: storeLinks('Moss & Mortar', 'Field Notes'), createdAt: iso() });
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
      radio: this.radioSeeds?.get(q.id) ?? null,
      ...(withTracks ? { tracks } : {}),
    };
  }
  private smartEval(d: SmartDefinition): string[] {
    let t = this.lib.tracks.slice();
    for (const r of d.rules) {
      if (!('field' in r) || r.op !== 'is') continue;
      if (r.field === 'genre') t = t.filter((x) => (x.genre ?? '').toLowerCase() === String(r.value).toLowerCase());
      if (r.field === 'artist') t = t.filter((x) => x.artist.toLowerCase() === String(r.value).toLowerCase());
    }
    if (d.limit?.type === 'songs') t = t.slice(0, d.limit.value);
    return t.map((x) => x.id);
  }
  private smartOut(sp: SmartPlaylist): SmartPlaylist {
    const ids = this.smartEval(sp.definition);
    return { ...sp, trackCount: ids.length, durationS: ids.length * 200, tracks: this.tracksOf(ids) };
  }
  private plOut(p: MockBackend['playlists'][number], withTracks = true): Playlist {
    const tracks = this.tracksOf(p.trackIds);
    return { id: p.id, name: p.name, createdAt: p.createdAt, updatedAt: p.updatedAt, trackCount: tracks.length, durationS: tracks.reduce((s, t) => s + t.durationS, 0), role: 'owner', rev: 0, memberCount: 0, collaborative: false, ...(withTracks ? { tracks } : {}) };
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
    const body = typeof init.body === 'string' && init.body ? JSON.parse(init.body) : {};
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    try {
      const out = this.route(method, path, url.searchParams, body, token);
      if (out === undefined) return new Response(null, { status: 204 });
      return new Response(JSON.stringify(out), { status: 200, headers: { 'Content-Type': 'application/json' } });
    } catch (e) {
      const he = e instanceof HttpError ? e : e instanceof AdminError ? new HttpError(e.status, e.detail) : new HttpError(500, String(e));
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
    if (path === '/me/scrobble' || path.startsWith('/me/scrobble/')) return this.scrobbleMock(path, method, body);
    if (path === '/me/import' || path.startsWith('/me/import/')) return this.importMock(path, method);
    if (path === '/me/notifications') return { items: this.notes, unread: this.notes.filter((n) => !n.read).length };
    if (path === '/me/notifications/read') {
      const n = this.notes.filter((x) => !x.read).length;
      this.notes = this.notes.map((x) => ({ ...x, read: true }));
      return { marked: n, unread: 0 };
    }
    if (path === '/library/new') {
      const albums = L.albums.slice(0, 2);
      return { since: iso(), days: 7, albums, tracks: L.tracks.filter((t) => !albums.some((a) => a.id === t.albumId)).slice(0, 3).map((t) => this.pub(t)) };
    }
    if (path === '/me/family') {
      if (method === 'PUT') {
        const b = body as { enabled?: boolean; hideProbable?: boolean; hideUnknown?: boolean };
        for (const k of ['enabled', 'hideProbable', 'hideUnknown'] as const) if (typeof b[k] === 'boolean') this.family[k] = b[k]!;
      }
      return this.family;
    }
    if (path === '/me/voice/tokens') {
      if (method === 'POST') {
        const t = { id: `vt${this.voiceTokens.length + 1}`, name: String(body.name || 'Home Assistant'), createdAt: iso(), lastUsed: null };
        this.voiceTokens.unshift(t);
        return { ...t, token: `nmv_mock${Math.random().toString(36).slice(2)}` };
      }
      return { tokens: this.voiceTokens };
    }
    if ((r = m(/^\/me\/voice\/tokens\/([^/]+)$/)) && method === 'DELETE') {
      this.voiceTokens = this.voiceTokens.filter((t) => t.id !== decodeURIComponent(r![1]));
      return undefined;
    }
    if (path === '/me/voice/devices')
      return {
        devices: this.hub.devices().filter((d) => d.canPlay).map((d) => ({ id: d.id, name: d.name, kind: d.kind, online: d.online, ...(this.voicePrefs.get(d.id) ?? { aliases: [], isDefault: false }) })),
      };
    if ((r = m(/^\/me\/voice\/devices\/(.+)$/)) && method === 'PUT') {
      const id = decodeURIComponent(r[1]);
      const cur = this.voicePrefs.get(id) ?? { aliases: [], isDefault: false };
      const b = body as { aliases?: string[]; isDefault?: boolean };
      if (b.isDefault) for (const v of this.voicePrefs.values()) v.isDefault = false;
      const next = { aliases: b.aliases ?? cur.aliases, isDefault: b.isDefault ?? cur.isDefault };
      this.voicePrefs.set(id, next);
      return { id, ...next };
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
    if ((r = m(/^\/library\/albums\/([^/]+)\/completeness(\/toget)?$/))) {
      // Demo: every album is missing its "track 3" bonus, which can go on To-get.
      const a = L.albums.find((x) => x.id === r![1]);
      if (!a) throw new HttpError(404, 'not found');
      const have = L.tracks.filter((t) => t.albumId === a.id).length;
      const title = `${a.title} (Demo Mix)`;
      if (r[2]) this.route('POST', '/toget', qs, { type: 'track', title, artist: a.artist, note: `Missing from ${a.title}` }, token);
      const tg = this.toget.find((x) => x.type === 'track' && x.title === title && x.artist === a.artist);
      return {
        albumId: a.id, status: 'incomplete', have, total: have + 1, releaseTitle: a.title, discCount: 1,
        missing: [{ disc: 1, position: have + 1, title, durationS: 214, recordingMbid: `rec-${a.id}`, toget: tg ? { id: tg.id, status: tg.status } : null }],
      };
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

    // AutoEq (a tiny fixed index in the demo)
    if (path === '/eq/autoeq') {
      const words = (qs.get('q') ?? '').toLowerCase().split(/\s+/).filter(Boolean);
      const results = MOCK_AUTOEQ.filter((e) => words.every((w) => e.name.toLowerCase().includes(w))).map(({ preampDb: _p, filters: _f, ...e }) => e);
      return { total: MOCK_AUTOEQ.length, results };
    }
    if (path === '/eq/autoeq/profile') {
      const p = MOCK_AUTOEQ.find((e) => e.id === qs.get('id'));
      if (!p) throw new HttpError(404, 'no such AutoEq profile');
      return p;
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
      const fileSrc = hashSource(id) ? 'lrc_file' : id.endsWith('2') ? 'lrclib' : 'embedded';
      const raw = own ?? (want === 'user' || (want && want !== fileSrc) ? undefined : L.lyrics.get(id));
      if (raw == null) throw new HttpError(404, `no lyrics for ${t.fileName}`);
      const p = parseLrc(raw);
      return { trackId: id, synced: p.synced, source: own != null ? 'user' : fileSrc, sources: [...(L.lyrics.has(id) ? [fileSrc] : []), ...(own != null ? ['user'] : [])], lines: p.lines, text: p.text, canEdit: true };
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

    // v7 smart playlists (mock: rules are not evaluated; genre/artist "is" and limits are)
    if (path === '/smart-playlists/fields') return MOCK_SMART_FIELDS;
    if (path === '/smart-playlists/preview') {
      const ids = this.smartEval(body.definition as SmartDefinition);
      return { trackCount: ids.length, durationS: ids.length * 200, tracks: this.tracksOf(ids.slice(0, Number(body.sample ?? 25))) };
    }
    if (path === '/smart-playlists') {
      if (method === 'POST') {
        const sp: SmartPlaylist = { id: nid('sp'), name: String(body.name), definition: body.definition as SmartDefinition, preset: (body.preset as string) ?? null, trackCount: 0, durationS: 0, rev: 0, createdAt: iso(), updatedAt: iso() };
        this.smart.push(sp);
        return this.smartOut(sp);
      }
      return this.smart.map((x) => ({ ...this.smartOut(x), tracks: undefined }));
    }
    if ((r = m(/^\/smart-playlists\/([^/]+)(\/refresh)?$/))) {
      const sp = this.smart.find((x) => x.id === r![1]);
      if (!sp) throw new HttpError(404, 'no such smart playlist');
      if (method === 'DELETE') {
        this.smart = this.smart.filter((x) => x !== sp);
        return undefined;
      }
      if (method === 'PATCH') {
        if (body.name) sp.name = String(body.name);
        if (body.definition) sp.definition = body.definition as SmartDefinition;
      }
      return this.smartOut(sp);
    }
    if ((r = m(/^\/library\/artists\/([^/]+)\/(page|info|tracks)$/))) {
      const a = L.artists.find((x) => x.id === r![1]);
      if (!a) throw new HttpError(404, 'not found');
      const tracks = L.tracks.filter((t) => t.artistId === a.id);
      if (r[2] === 'tracks') return tracks.map((t) => this.pub(t));
      const info = { status: 'ok', genres: ['indie', 'folk'], yearsActive: { from: 2009, to: null }, area: 'Somewhere', links: [{ kind: 'musicbrainz', url: 'https://musicbrainz.org/' }],
        bio: { text: `${a.name} is a mock artist made up for the offline demo.`, source: 'Wikipedia', url: 'https://en.wikipedia.org/', license: 'CC BY-SA 4.0', licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/' } };
      if (r[2] === 'info') return info;
      const albums = L.albums.filter((x) => x.artistId === a.id);
      return {
        id: a.id, name: a.name, mbid: null, artUrl: albums[0] ? `art/${albums[0].id}` : null, photoUrl: null, info,
        stats: { trackCount: tracks.length, durationS: 0, playCount: 0, albumCount: albums.length, singleCount: 0 },
        topTracks: tracks.slice(0, 10).map((t) => ({ ...this.pub(t), playCount: this.played.get(t.id)?.count ?? 0 })),
        albums, singles: [], appearsOn: [],
        similar: L.artists.filter((x) => x.id !== a.id).slice(0, 6).map((x, i) => ({ ...x, similarity: 0.9 - i * 0.05, artUrl: null })),
        radioSeed: { type: 'artist', id: a.id, title: a.name },
      };
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
    // v5 collaboration (mock: single-user, owner only)
    if ((r = m(/^\/playlists\/([^/]+)\/members$/))) {
      this.getP(r[1]);
      return [{ userId: me.id, username: me.username, role: 'owner' }];
    }
    if (m(/^\/playlists\/([^/]+)\/m3u$/)) return '#EXTM3U\n';
    if ((r = m(/^\/playlists\/([^/]+)\/activity$/))) {
      this.getP(r[1]);
      return [];
    }
    if (path === '/users') return this.users.filter((u) => u.id !== me.id).map((u) => ({ id: u.id, username: u.username }));
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
    if (path === '/release-radar') return this.releaseRadar();
    if (path === '/wrapped/years') return [new Date().getFullYear(), new Date().getFullYear() - 1];
    if (path === '/wrapped' || path === '/admin/wrapped') return this.wrapped(Number(qs.get('year')) || new Date().getFullYear(), path !== '/wrapped');
    if (path === '/blends/candidates') return this.users.filter((u) => u.id !== me.id).map((u) => ({ userId: u.id, username: u.username }));
    if (path === '/blends' && method === 'POST') {
      const b = this.mockBlend(String(body.name || '') || `${me.username} + ${(body.usernames as string[]).join(' + ')}`);
      this.blendList = [b, ...this.blendList];
      return b;
    }
    if (path === '/blends') return this.blendList.map((b) => ({ ...b, tracks: [] }));
    const bm = /^\/blends\/([^/]+)(?:\/(accept|decline|leave|refresh|invite))?$/.exec(path);
    if (bm) {
      const b = this.blendList.find((x) => x.id === bm[1]);
      if (!b) throw new HttpError(404, 'no such blend');
      if (bm[2] === 'leave' || bm[2] === 'decline') {
        this.blendList = this.blendList.filter((x) => x !== b);
        return null;
      }
      if (bm[2] === 'accept') b.myStatus = 'joined';
      if (method === 'PATCH' && body.name) b.name = String(body.name);
      return b.myStatus === 'invited' ? { ...b, tracks: [] } : b;
    }
    if (path === '/search') return this.search(qs.get('q') ?? '', qs.get('scope') ?? 'all');
    if (path === '/toget') {
      if (method === 'POST') {
        const b = body as { catalogId?: string; type?: 'album' | 'track'; title?: string; artist?: string; year?: number; note?: string };
        const c = b.catalogId ? this.catalog.find((x) => x.id === b.catalogId) : undefined;
        if (b.catalogId && !c) throw new HttpError(404, 'unknown catalogId');
        if (!c && !(b.type && b.title && b.artist)) throw new HttpError(422, 'catalogId or type+title+artist required');
        const existing = this.toget.find((x) => (b.catalogId && x.catalogId === b.catalogId) || (x.title === (c?.title ?? b.title) && x.artist === (c?.artist ?? b.artist)));
        if (existing) {
          if (existing.status === 'dismissed') existing.status = 'want';
          return existing;
        }
        const title = c?.title ?? b.title!;
        const artist = c?.artist ?? b.artist!;
        const item: ToGetItem = {
          id: nid('tg'),
          type: c ? (c.type === 'track' ? 'track' : 'album') : b.type!,
          title,
          artist,
          year: c?.year ?? b.year ?? null,
          catalogId: c?.id ?? null,
          artUrl: c?.artUrl ?? null,
          hasPreview: !!c?.hasPreview,
          status: c?.owned ? 'got' : 'want',
          source: c ? 'search' : 'manual',
          note: b.note ?? null,
          links: storeLinks(artist, title),
          createdAt: iso(),
        };
        this.toget.unshift(item);
        return item;
      }
      const st = qs.get('status') ?? 'want';
      const mark = (x: ToGetItem) => {
        const a = this.wantMarks.get(x.id);
        return { ...x, adminAdded: a ? { at: a.at, note: a.note } : null };
      };
      return (st === 'all' ? this.toget : this.toget.filter((x) => x.status === st)).map(mark);
    }
    if ((r = m(/^\/toget\/([^/]+)$/))) {
      const item = this.toget.find((x) => x.id === r![1]);
      if (!item) throw new HttpError(404, 'not found');
      if (method === 'DELETE') {
        this.toget = this.toget.filter((x) => x !== item);
        return undefined;
      }
      if (body.status) {
        item.status = body.status as ToGetItem['status'];
        item.gotAt = item.status === 'got' ? iso() : null;
      }
      if ('note' in body) item.note = (body.note as string) ?? null;
      item.updatedAt = iso();
      return item;
    }
    if (path === '/pins') {
      if (method === 'POST') {
        const type = body.type as PinType;
        const id = String(body.id);
        this.pinsList = this.pinsList.filter((p) => !(p.type === type && p.id === id));
        const pos = typeof body.position === 'number' ? body.position : this.pinsList.length;
        this.pinsList.splice(pos, 0, { type, id });
      }
      return this.pinsList.map((p) => this.item(p.type, p.id));
    }
    if ((r = m(/^\/continue\/(queue|episode|book)\/([^/]+)$/)) && method === 'DELETE') return undefined;
    if ((r = m(/^\/pins\/([a-z_]+)\/([^/]+)$/)) && method === 'DELETE') {
      this.pinsList = this.pinsList.filter((p) => !(p.type === r![1] && p.id === decodeURIComponent(r![2])));
      return undefined;
    }
    // 0.8.2 radio DJ voice: the mock has no voice (no audio in mock mode), but the switch and settings work.
    if (path === '/dj/status') return { state: 'unavailable', voice: 'en_GB-cori-medium', detail: 'mock server', enabled: this.settings['dj.voice'] === true, every: typeof this.settings['dj.every'] === 'number' ? this.settings['dj.every'] : 3 };
    if (path === '/dj/intro' && method === 'POST') throw new HttpError(503, "the DJ voice isn't ready: mock server");
    if (path === '/radio/vibe/parse' && method === 'POST') {
      const text = String(body.text ?? '');
      const words = text.toLowerCase();
      const constraints: VibeConstraint[] = [];
      if (/chill|calm|rain|relax/.test(words)) constraints.push({ id: 'energy:-0.45', kind: 'energy', label: 'Low energy', max: 0.45 }, { id: 'mood:chill:+', kind: 'mood', label: 'Chill', value: 'chill', weight: 1 });
      if (/gym|workout|upbeat|no slow/.test(words)) constraints.push({ id: 'energy:0.65-', kind: 'energy', label: 'High energy', min: 0.65 });
      if (/no lyrics|instrumental/.test(words)) constraints.push({ id: 'vocals:instrumental', kind: 'vocals', label: 'No vocals', value: 'instrumental' });
      const dec = /(\d0)s/.exec(words);
      if (dec) {
        const y = Number(dec[1].length === 2 ? (Number(dec[1]) >= 30 ? '19' : '20') + dec[1] : dec[1]);
        constraints.push({ id: `year:${y}-${y + 9}`, kind: 'year', label: `${y}s`, min: y, max: y + 9 });
      }
      return { text, title: text, constraints };
    }
    if (path === '/radio' && method === 'POST') {
      const seed = (body.seed ?? { type: 'random' }) as RadioSeed;
      const name =
        seed.type === 'random' ? 'Random radio' : seed.type === 'moment' ? 'Smart shuffle' : seed.type === 'vibe' ? `${seed.text || 'Vibe'} radio` : `${this.describe(seed.type, seed.id ?? '').title} radio`;
      const pickSeed = seed.type === 'moment' || seed.type === 'vibe' ? { type: 'random' as const } : (seed as { type: 'random' | 'track' | 'artist' | 'album' | 'playlist'; id?: string });
      const picks = this.radioPicks(pickSeed, new Set(), 25);
      const q = this.createQueue(name, picks, 0);
      this.radioSeen.set(q.id, new Set(picks));
      const explanation = seed.type === 'moment' ? 'Because it’s this time of day and you’ve been playing upbeat music (demo).' : seed.type === 'vibe' ? `Vibe: ${(seed.constraints ?? []).map((c) => c.label).join(', ') || 'anything'}.` : null;
      this.radioSeeds.set(q.id, { ...seed, title: name.replace(/ radio$/, ''), explanation });
      return this.queueOut(q);
    }
    if ((r = m(/^\/radio\/([^/]+)\/(station|steer)$/))) {
      const q = this.getQ(r[1]);
      const seed = this.radioSeeds.get(q.id);
      if (!seed) throw new HttpError(404, 'not a radio queue');
      const st = this.radioSteering.get(q.id) ?? { energy: 0, variety: 0, moreLike: [], lessLike: [], never: [] };
      if (r[2] === 'steer') {
        const a = String(body.action);
        if (a === 'more_energy') st.energy = Math.min(3, st.energy + 1);
        if (a === 'calmer') st.energy = Math.max(-3, st.energy - 1);
        if (a === 'more_variety') st.variety = Math.min(2, st.variety + 1);
        if (a === 'more_familiar') st.variety = Math.max(-2, st.variety - 1);
        if (a === 'reset') Object.assign(st, { energy: 0, variety: 0, moreLike: [], lessLike: [], never: [] });
        const t = L.tracks.find((x) => x.id === body.trackId);
        if (t) {
          const ref = body.scope === 'artist' ? { type: 'artist' as const, id: t.artistId, name: t.artist } : { type: 'track' as const, id: t.id, name: t.title };
          if (a === 'more_like') st.moreLike.push(ref);
          if (a === 'less_like') st.lessLike.push(ref);
          if (a === 'never_here') st.never.push(ref);
        }
        if (a === 'remove') for (const k of ['moreLike', 'lessLike', 'never'] as const) st[k] = st[k].filter((x) => x.id !== body.refId);
        this.radioSteering.set(q.id, st);
      }
      const summary = [st.energy > 0 ? 'more energy' : st.energy < 0 ? 'calmer' : '', st.variety > 0 ? 'more variety' : st.variety < 0 ? 'more familiar' : ''].filter(Boolean).join(', ') || null;
      seed.steering = summary;
      const station: RadioStation = { queueId: q.id, radioId: q.id, kind: seed.type, title: q.name, stationKey: seed.type, explanation: seed.explanation, text: seed.text, constraints: seed.constraints, steeringSummary: summary, steering: st };
      return r[2] === 'steer' ? { station, queue: this.queueOut(q) } : station;
    }
    if ((r = m(/^\/radio\/([^/]+)\/more$/))) {
      const q = this.getQ(r[1]);
      if (!this.radioSeeds.has(q.id)) throw new HttpError(404, 'not a radio queue');
      const seen = this.radioSeen.get(q.id) ?? new Set(q.trackIds);
      const more = this.radioPicks({ type: 'track', id: q.trackIds.at(-1) }, seen, Number(body.count ?? 15));
      more.forEach((id) => seen.add(id));
      q.trackIds.push(...more);
      q.updatedAt = iso();
      return this.queueOut(q);
    }

    // admin
    if (path.startsWith('/admin/') && !me.isAdmin) throw new HttpError(403, 'Admins only');
    const A = this.admin;
    if (path.startsWith('/admin/wants')) return this.adminWants(method, path, qs, body, me);
    if (path === '/admin/family') return this.users.map((u) => ({ userId: u.id, username: u.username, isAdmin: u.isAdmin, lockedBy: null, ...this.family }));
    if ((r = m(/^\/admin\/users\/([^/]+)\/family$/))) {
      const u = this.users.find((x) => x.id === r![1]);
      if (!u) throw new HttpError(404, 'no such user');
      return { userId: u.id, username: u.username, isAdmin: u.isAdmin, lockedBy: null, ...this.family, ...(body as object) };
    }
    if (path === '/admin/explicit') {
      if (method === 'POST') return { batchId: 'b', changed: 0, tracks: ((body.trackIds as string[]) ?? []).length };
      const items = L.tracks.slice(0, 20).map((t) => ({ trackId: t.id, title: t.title, artist: t.artist, album: t.album, albumId: t.albumId,
        status: 'unknown', source: null, admin: null, tags: null, deezer: null, lyrics: null, lyricsHits: null }));
      return { total: items.length, counts: { explicit: 0, probable: 0, clean: 0, unknown: L.tracks.length }, items };
    }
    if (path === '/admin/status') return A.status({ tracks: L.tracks.length, albums: L.albums.length, artists: L.artists.length });
    if (path === '/admin/now-playing') {
      const st = this.hub.state;
      const t = L.tracks.find((x) => x.id === st.trackId);
      return this.hub
        .devices()
        .filter((d) => d.online)
        .map((d) => ({ userId: 'u1', username: 'admin', deviceId: d.id, deviceName: d.name, kind: d.kind, playing: d.isActive && st.playing, positionMs: d.isActive ? st.positionMs : 0, track: d.isActive && t ? this.pub(t) : null }));
    }
    if ((r = m(/^\/admin\/jobs\/([a-z]+)\/run$/)) && method === 'POST') return A.runJob(r[1]);
    if (path === '/admin/jobs/history') return A.history.slice(0, Number(qs.get('limit') ?? 50));
    if ((r = m(/^\/admin\/users\/([^/]+)\/devices$/)))
      return this.hub.devices().filter((d) => r![1] === 'u1' || d.id !== 'mock-phone').map((d) => ({ id: d.id, name: d.name, kind: d.kind, online: d.online, lastSeen: iso() }));
    if (path === '/admin/library/failed') return A.failed;
    if (path === '/admin/library/failed/retry') return A.retry();
    if ((r = m(/^\/admin\/library\/failed\/([^/]+)\/retry$/))) return A.retry(r[1]);
    if (path === '/admin/health') return A.health();
    if ((r = m(/^\/admin\/health\/([a-z_]+)\/fix$/)) && method === 'POST') return A.fix(r[1], (body.ids as string[] | undefined) ?? undefined);
    if ((r = m(/^\/admin\/health\/([a-z_]+)$/))) return A.healthItems(r[1]);
    if (path === '/admin/logs') return A.logsAfter(Number(qs.get('after') ?? 0), qs.get('level'), qs.get('q'), Number(qs.get('limit') ?? 500));
    if (path === '/admin/reports') return A.reports.map(({ description: _d, logs: _l, context: _c, ...x }) => x);
    if ((r = m(/^\/admin\/reports\/([^/]+)$/))) {
      const rep = A.reports.find((x) => x.id === r![1]);
      if (!rep) throw new HttpError(404, 'not found');
      if (method === 'DELETE') {
        A.reports = A.reports.filter((x) => x !== rep);
        return undefined;
      }
      if (method === 'PATCH' && body.status) rep.status = body.status as 'open' | 'closed';
      return rep;
    }
    if (path === '/admin/autotag/status') return A.autotagStatus();
    if (path === '/admin/autotag/groups') return A.autotagGroups(qs.get('status') ?? 'pending');
    if (path === '/admin/autotag/accept' && method === 'POST')
      return {
        accepted: A.autotagDecide(body, 'accepted'),
        batchId: 'mock',
        changed: 1,
        coverErrors: [],
      };
    if (path === '/admin/autotag/reject' && method === 'POST') return { rejected: A.autotagDecide(body, 'rejected') };
    if (path === '/admin/autotag/reopen' && method === 'POST')
      return {
        reopened: A.autotagDecide(body, 'pending', 'accepted') + A.autotagDecide(body, 'pending', 'rejected'),
        reverted: 0,
      };
    if (path === '/admin/autotag/rematch' && method === 'POST') return { queued: (body.trackIds as string[]).length };
    if (path === '/admin/duplicates') return A.dups;
    if (path === '/admin/duplicates/resolve' && method === 'POST') return A.resolveDup(body.keepTrackId as string, body.hideTrackIds as string[]);
    if (path === '/admin/duplicates/unhide' && method === 'POST') return A.unhideDup(body.trackIds as string[]);
    if (path === '/admin/backups') {
      if (method === 'POST') {
        const b = { id: 'b' + Date.now(), createdAt: iso(), bytes: 49e6, kind: 'manual', note: (body.note as string) ?? null };
        A.backups.unshift(b);
        return b;
      }
      return A.backups;
    }
    if (path === '/admin/backups/offsite') return A.offsite;
    if ((r = m(/^\/admin\/backups\/([^/]+)\/offsite$/)) && method === 'POST') {
      const name = r[1] + '.db.gz';
      A.offsite.items.unshift({ name, kind: 'manual', bytes: 16e6, createdAt: iso() });
      A.offsite.last = { at: iso(), ok: true, error: null, name };
      return { name, destination: A.offsite.destination, configCopied: true, pruned: 0 };
    }
    if ((r = m(/^\/admin\/backups\/([^/]+)\/restore$/)) && method === 'POST') {
      const safety = { id: 'b' + Date.now(), createdAt: iso(), bytes: 49e6, kind: 'prerestore' };
      A.backups.unshift(safety);
      return { restored: r[1], source: body.source ?? 'local', schemaFrom: 8, schemaTo: 8, safetyBackup: safety.id };
    }
    if ((r = m(/^\/admin\/backups\/([^/]+)$/)) && method === 'DELETE') {
      A.backups = A.backups.filter((b) => b.id !== r![1]);
      return undefined;
    }
    if (path === '/admin/settings') {
      if (method === 'PUT') return A.saveSettings(body as Partial<import('./admin').AdminSettings>);
      return A.settings;
    }
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
    if (path === '/admin/users') return this.users.map(({ password: _p, ...u }) => ({ ...u, lastLogin: u.id === 'u1' ? iso() : null, deviceCount: u.id === 'u1' ? this.hub.devices().length : 0 }));
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

  /** A ShelfItem as the server would resolve it (artUrl relative to the API base). */
  private item(type: ShelfItem['type'], id: string): ShelfItem {
    const d = this.describe(type, id);
    return { type, id, title: d.title, subtitle: d.subtitle, artUrl: d.albumId ? `art/${d.albumId}` : null };
  }

  describe(type: string, id: string): { title: string; subtitle: string | null; albumId: string | null } {
    const L = this.lib;
    if (type === 'album') {
      const a = L.albums.find((x) => x.id === id);
      return { title: a?.title ?? id, subtitle: a ? `Album · ${a.artist}` : 'Album', albumId: id };
    }
    if (type === 'artist') {
      const a = L.artists.find((x) => x.id === id);
      return { title: a?.name ?? id, subtitle: 'Artist', albumId: L.albums.find((x) => x.artistId === id)?.id ?? null };
    }
    if (type === 'playlist') {
      const p = this.playlists.find((x) => x.id === id);
      return { title: p?.name ?? id, subtitle: 'Playlist', albumId: L.tracks.find((t) => t.id === p?.trackIds[0])?.albumId ?? null };
    }
    if (type === 'mix') {
      const m = this.mixes.find((x) => x.id === id);
      return { title: m?.title ?? id, subtitle: 'Mix', albumId: m?.tracks[0]?.track.albumId ?? null };
    }
    if (type === 'genre') return { title: id, subtitle: 'Genre', albumId: L.tracks.find((t) => t.genre === id)?.albumId ?? null };
    if (type === 'queue') {
      const q = this.queues.find((x) => x.id === id);
      return { title: q?.name ?? id, subtitle: 'Queue', albumId: L.tracks.find((t) => t.id === q?.trackIds[q.currentIndex])?.albumId ?? null };
    }
    const t = L.tracks.find((x) => x.id === id);
    return { title: t?.title ?? id, subtitle: t?.artist ?? null, albumId: t?.albumId ?? null };
  }

  private radioPicks(seed: { type: string; id?: string | null }, seen: Set<string>, n: number): string[] {
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

  private homeShelves(deviceId: string): Shelf[] {
    const L = this.lib;
    const mix = this.mixes[0];
    const recent: ShelfItem[] = [
      this.item('album', L.albums[0].id),
      { type: 'mix', id: mix.id, title: mix.title, subtitle: 'Mix', artUrl: `art/${mix.tracks[0].track.albumId}`, mix: { kind: mix.kind, trackCount: mix.tracks.length } },
      this.item('playlist', 'p1'),
      this.item('album', L.albums[3].id),
      this.item('album', L.albums[5].id),
      { type: 'radio', id: 'radio:artist:ar2', title: `${L.artists[2].name} radio`, subtitle: 'Radio', artUrl: `art/${L.albums[4].id}`, seed: { type: 'artist', id: L.artists[2].id } },
      this.item('playlist', 'p2'),
      this.item('album', L.albums[8].id),
    ];
    const cont: ShelfItem[] = [
      { ...this.item('album', L.albums[2].id), progress: { index: 2, count: L.albums[2].trackCount, positionMs: 30000, queueId: null } },
      ...this.queues
        .filter((q) => q.currentIndex > 0 || q.positionMs > 0)
        .map((q) => ({ ...this.item('queue', q.id), progress: { index: q.currentIndex, count: q.trackIds.length, positionMs: q.positionMs, queueId: q.id } })),
    ];
    const devices: ShelfItem[] = this.hub
      .devices()
      .filter((d) => d.online && d.id !== deviceId)
      .map((d) => ({
        type: 'device' as const,
        id: d.id,
        title: d.name,
        subtitle: d.isActive ? 'Playing' : 'Idle',
        device: { kind: d.kind, canPlay: d.canPlay, isActive: d.isActive, playing: d.isActive && this.hub.state.playing, nowPlaying: d.isActive ? this.pubTrackById(this.hub.state.trackId) : null },
      }));
    const shelves: Shelf[] = [
      { id: 'start_radio', kind: 'start_radio', title: 'Start radio', layout: 'button', items: [{ type: 'radio' as const, id: 'radio:random', title: 'Start a random radio', subtitle: 'Seeded from your taste', seed: { type: 'random' as const } }] },
      { id: 'devices', kind: 'devices', title: 'Your devices', layout: 'row', items: devices },
      { id: 'recently_played', kind: 'recently_played', title: 'Recently played', layout: 'grid2', items: recent },
      { id: 'continue_listening', kind: 'continue_listening', title: 'Continue listening', layout: 'row', items: cont },
      { id: 'pinned', kind: 'pinned', title: 'Pinned', layout: 'row', items: this.pinsList.map((p) => this.item(p.type, p.id)) },
      {
        id: 'on_this_day',
        kind: 'on_this_day',
        title: 'On this day',
        subtitle: `Around ${new Date().getDate()} ${new Date().toLocaleString('en-GB', { month: 'long' })} in earlier years`,
        layout: 'row',
        items: this.lib.tracks.slice(3, 9).map((t, i) => ({
          type: 'track' as const,
          id: t.id,
          title: t.title,
          subtitle: `${t.artist} · ${i < 3 ? '1 year ago' : '2 years ago'}, played ${6 - i} times`,
          artUrl: `art/${t.albumId}`,
          track: this.pub(t),
        })),
      },
    ];
    return shelves.filter((s) => s.items.length);
  }

  private pubTrackById(id: string | null): Track | null {
    const t = id ? this.lib.tracks.find((x) => x.id === id) : undefined;
    return t ? this.pub(t) : null;
  }

  private blendList: Blend[] = [];

  private mockBlend(name: string, invited = false): Blend {
    const t = this.lib.tracks.slice(0, 24);
    return {
      id: 'bl' + Math.random().toString(36).slice(2, 8),
      name,
      myStatus: invited ? 'invited' : 'joined',
      invitedBy: invited ? 'sam' : null,
      createdBy: { userId: 'u2', username: 'sam' },
      members: [
        { userId: 'u1', username: 'me', status: 'joined' },
        { userId: 'u2', username: 'sam', status: 'joined' },
      ],
      matchPct: 72,
      pairs: [{ a: 'u1', b: 'u2', aName: 'me', bName: 'sam', pct: 72 }],
      trackCount: t.length,
      tracks: t.map((x, i) => ({ track: this.pub(x), forUsers: i % 5 === 0 ? ['u1', 'u2'] : [i % 2 ? 'u2' : 'u1'], reason: i % 5 === 0 ? 'shared' : i % 3 ? 'top' : 'match' })),
    };
  }

  private releaseRadar(): ReleaseRadar {
    const L = this.lib;
    return {
      updatedAt: new Date(Date.now() - 86400e3).toISOString(),
      windowDays: 56,
      inLibrary: L.albums.slice(0, 4).map((a) => ({ type: 'album' as const, id: a.id, title: a.title, subtitle: a.artist, artUrl: `art/${a.id}`, album: a, release: { date: '2026-11-06', type: 'Album', upcoming: false, artist: a.artist } })),
      notOwned: L.artists.slice(2, 7).map((a, i) => {
        const id = `mb:rg:mock${i}`;
        const catalog = { id, type: 'album' as const, title: `New ${['LP', 'EP', 'Single', 'Album', 'Sessions'][i]}`, artist: a.name, year: 2026, hasPreview: false, owned: false, toGet: null, artUrl: null, links: [] };
        return { type: 'catalog' as const, id, title: catalog.title, subtitle: a.name, catalog, release: { date: i === 4 ? '2026-12-12' : `2026-11-${10 + i}`, type: i === 1 ? 'EP' : i === 2 ? 'Single' : 'Album', upcoming: i === 4, artist: a.name } };
      }),
    };
  }

  private wrapped(year: number, server: boolean): Wrapped & Partial<ServerWrapped> {
    const L = this.lib;
    const tracks = L.tracks.slice(0, 10);
    const byHour = Array.from({ length: 24 }, (_, h) => Math.round(40 * Math.exp(-((h - 19) ** 2) / 18) + 8 * Math.exp(-((h - 8) ** 2) / 6)));
    return {
      year,
      final: year < new Date().getFullYear(),
      totals: { minutes: 18432, plays: 5210, skips: 640, tracks: 1210, artists: 214, albums: 388, days: 301 },
      topTracks: tracks.slice(0, 5).map((t, i) => ({ track: this.pub(t), plays: 60 - i * 7, minutes: 240 - i * 25 })),
      topArtists: L.artists.slice(0, 5).map((a, i) => ({ artist: { id: a.id, name: a.name, albumCount: 1 } as never, artUrl: `art/${L.albums.find((x) => x.artistId === a.id)?.id}`, plays: 300 - i * 40, minutes: 1200 - i * 150 })),
      topAlbums: L.albums.slice(0, 5).map((a, i) => ({ album: a, plays: 120 - i * 15, minutes: 500 - i * 60 })),
      topGenres: [
        { name: 'Indie Rock', minutes: 6000, share: 0.33 },
        { name: 'Jazz', minutes: 4000, share: 0.22 },
        { name: 'Electronic', minutes: 3000, share: 0.16 },
      ],
      byHour,
      byWeekday: [10, 12, 11, 13, 20, 25, 18],
      byMonth: Array.from({ length: 12 }, (_, i) => 1000 + i * 50),
      peakHour: 19,
      peakWeekday: 5,
      topDay: { date: `${year}-07-14`, minutes: 412 },
      streaks: { longest: 47, longestFrom: `${year}-03-01`, longestTo: `${year}-04-16`, current: 9 },
      discoveries: { artists: 38, tracks: 410, share: 0.21, topArtists: [] },
      aura: { label: 'Golden', description: 'Feel-good and steady.', energy: 0.52, danceability: 0.61, valence: 0.2, bpm: 118, moods: [{ name: 'happy', score: 0.48 }, { name: 'relaxed', score: 0.41 }], colors: ['#d9a13b', '#c2417a'] },
      firstPlay: tracks[0] ? { track: this.pub(tracks[0]), at: `${year}-01-01T00:12:00Z` } : null,
      ...(server ? { listeners: 4, users: 5 } : {}),
    };
  }

  private forYouShelves(): Shelf[] {
    const L = this.lib;
    if (!this.blendList.length) this.blendList = [this.mockBlend('me + sam'), this.mockBlend('Road trip', true)];
    const rr = this.releaseRadar();
    const year = new Date().getFullYear();
    const seed = L.tracks[4];
    const similar = L.tracks.filter((t) => t.genre === seed.genre && t.id !== seed.id).slice(0, 10);
    const shelves: Shelf[] = [
      {
        id: 'mixes',
        kind: 'mixes',
        title: 'Your mixes',
        items: this.mixes.map((m) => ({ type: 'mix' as const, id: m.id, title: m.title, subtitle: `${m.tracks.length} songs`, artUrl: `art/${m.tracks[0].track.albumId}`, mix: { kind: m.kind, trackCount: m.tracks.length, durationS: m.durationS } })),
      },
      {
        id: 'radio',
        kind: 'radio',
        title: 'Radio',
        items: [
          { type: 'radio' as const, id: `radio:track:${seed.id}`, title: `${seed.title} radio`, subtitle: 'Song radio', artUrl: `art/${seed.albumId}`, seed: { type: 'track' as const, id: seed.id } },
          ...L.artists.slice(0, 5).map((a) => ({ type: 'radio' as const, id: `radio:artist:${a.id}`, title: `${a.name} radio`, subtitle: 'Artist radio', artUrl: `art/${L.albums.find((x) => x.artistId === a.id)?.id}`, seed: { type: 'artist' as const, id: a.id } })),
        ],
      },
      {
        id: `byp:track:${seed.id}`,
        kind: 'because_you_played',
        title: `Because you played ${seed.title}`,
        items: similar.map((t) => ({ type: 'track' as const, id: t.id, title: t.title, subtitle: t.artist, artUrl: `art/${t.albumId}`, track: this.pub(t) })),
      },
      {
        id: 'suggestions',
        kind: 'suggestions',
        title: 'Suggestions',
        items: mockSuggestions.filter((x) => x.status === 'new').map((x) => ({ type: 'suggestion' as const, id: x.id, title: x.album, subtitle: x.artist, artUrl: `suggestions/${x.id}/cover`, suggestion: x })),
      },
      {
        id: 'to_get',
        kind: 'to_get',
        title: 'To get',
        items: this.toget.filter((x) => x.status === 'want').map((x) => ({ type: 'toget' as const, id: x.id, title: x.title, subtitle: x.artist, artUrl: x.artUrl, toget: x })),
      },
      { id: 'release_radar', kind: 'release_radar', title: 'Release Radar', subtitle: 'New from artists you play', items: rr.notOwned },
      { id: 'release_radar:library', kind: 'release_radar', title: 'New in your library', subtitle: 'Recently added by artists you play', items: rr.inLibrary },
      {
        id: 'blends',
        kind: 'blends',
        title: 'Blends',
        items: [
          ...this.blendList.map((b) => ({ type: 'blend' as const, id: b.id, title: b.name, subtitle: b.myStatus === 'invited' ? `Invite from ${b.invitedBy}` : `${b.matchPct}% match`, artUrl: null, blend: { status: b.myStatus, matchPct: b.matchPct, trackCount: b.trackCount } })),
          { type: 'blend' as const, id: 'new', title: 'Start a blend', subtitle: "Mix your taste with someone else's", artUrl: null, blend: { status: 'new' as const } },
        ],
      },
      { id: 'wrapped', kind: 'wrapped', title: 'Wrapped', subtitle: 'Your year in music', items: [{ type: 'wrapped' as const, id: String(year), title: `Your ${year} so far`, subtitle: '5,210 plays', artUrl: null, wrapped: { year, final: false } }] },
    ];
    return shelves.filter((s) => s.items.length);
  }

  pubTrack(t: TrackInfo): Track {
    return this.pub(t);
  }

  private search(q: string, scope: string) {
    const term = q.toLowerCase().trim();
    const L = this.lib;
    const lyrics = this.lyricMatches(term, scope === 'lyrics' ? 50 : 5);
    if (scope === 'lyrics') return { query: q, top: null, tracks: [], albums: [], artists: [], playlists: [], queues: [], catalog: [], catalogError: null, lyrics };
    const lib = scope !== 'catalog';
    const tracks = lib ? L.tracks.filter((t) => t.title.toLowerCase().includes(term) || t.artist.toLowerCase().includes(term)).slice(0, 20).map((t) => this.pub(t)) : [];
    const albums = lib ? L.albums.filter((a) => a.title.toLowerCase().includes(term) || a.artist.toLowerCase().includes(term)) : [];
    const artists = lib ? L.artists.filter((a) => a.name.toLowerCase().includes(term)) : [];
    const playlists = lib ? this.playlists.filter((p) => p.name.toLowerCase().includes(term)).map((p) => this.plOut(p, false)) : [];
    const queues = lib ? this.queues.filter((x) => x.name.toLowerCase().includes(term)).map((x) => this.queueOut(x, false)) : [];
    const catalog =
      scope === 'library'
        ? []
        : this.catalog
            .filter((c) => (scope === 'catalog' || !c.owned) && (c.title.toLowerCase().includes(term) || (c.artist ?? '').toLowerCase().includes(term)))
            .map((c) => {
              const tg = this.toget.find((x) => x.catalogId === c.id);
              return { ...c, toGet: tg ? { id: tg.id, status: tg.status } : null };
            });
    const top: ShelfItem | null = artists[0]
      ? { ...this.item('artist', artists[0].id), artist: artists[0] }
      : albums[0]
        ? { ...this.item('album', albums[0].id), album: albums[0] }
        : tracks[0]
          ? { type: 'track', id: tracks[0].id, title: tracks[0].title, subtitle: tracks[0].artist, artUrl: `art/${tracks[0].albumId}`, track: tracks[0] }
          : catalog[0]
            ? { type: 'catalog', id: catalog[0].id, title: catalog[0].title, subtitle: catalog[0].artist, artUrl: catalog[0].artUrl, catalog: catalog[0] }
            : null;
    return { query: q, top, tracks, albums, artists, playlists, queues, catalog, catalogError: null, lyrics: lib ? lyrics : [] };
  }

  /** Lyrics search like the server's: every word somewhere in the song's words; the best line comes back. */
  private lyricMatches(term: string, limit: number) {
    const words = term.split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const out: { track: Track; line: string }[] = [];
    for (const [id, raw] of this.lib.lyrics) {
      const lines = raw.split('\n').map((l) => l.replace(/\[[^\]]*\]|<[^>]*>/g, '').trim()).filter(Boolean);
      const text = lines.join(' ').toLowerCase();
      if (!words.every((w) => text.includes(w))) continue;
      const score = (l: string) => words.filter((w) => l.toLowerCase().includes(w)).length;
      const line = lines.reduce((a, b) => (score(b) > score(a) ? b : a), lines[0]);
      const t = this.pubTrackById(id);
      if (t) out.push({ track: t, line });
      if (out.length >= limit) break;
    }
    return out;
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
    catalogCover: async (id) => artSvg(id, this.catalog.find((c) => c.id === id)?.title ?? '?'),
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
    const devs: ConnectDevice[] = [...this.known].map(([id, d]) => ({ id, name: d.name, kind: d.kind, canPlay: true, online: online.has(id), isActive: this.state.activeDeviceId === id }));
    // A server-side speaker, so the picker can be tried in mock mode (it doesn't play anything).
    devs.push({ id: 'spk:chromecast:mock', name: 'Living room speaker', kind: 'speaker', castType: 'chromecast', canPlay: true, online: true, isActive: this.state.activeDeviceId === 'spk:chromecast:mock' });
    return devs;
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
    const speaker = to.startsWith('spk:');
    this.state = { ...this.state, activeDeviceId: to, positionMs, positionAt: iso(), playing: speaker ? false : play, connecting: speaker, rev: this.state.rev + 1 };
    if (speaker) {
      // Like the server: "Connecting…" for a moment, then it plays (the mock speaker always connects).
      setTimeout(() => {
        if (this.state.activeDeviceId === to && this.state.connecting) this.setState({ connecting: false, playing: play, positionMs, positionAt: iso() });
      }, 1500);
    }
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

const MOCK_AUTOEQ = [
  {
    id: 'oratory1990/over-ear/Sennheiser HD 650', name: 'Sennheiser HD 650', source: 'oratory1990', rig: null, form: 'over-ear', preampDb: -6.1,
    filters: [
      { type: 'LSC' as const, fc: 105, gainDb: 6.4, q: 0.7 },
      { type: 'PK' as const, fc: 8800, gainDb: 5.1, q: 1.42 },
      { type: 'PK' as const, fc: 118, gainDb: -3.1, q: 0.5 },
      { type: 'HSC' as const, fc: 10000, gainDb: -2.1, q: 0.7 },
    ],
  },
  {
    id: 'oratory1990/in-ear/Apple AirPods Pro', name: 'Apple AirPods Pro', source: 'oratory1990', rig: null, form: 'in-ear', preampDb: -4.2,
    filters: [{ type: 'PK' as const, fc: 200, gainDb: -3.5, q: 0.8 }, { type: 'PK' as const, fc: 3000, gainDb: 3.2, q: 1.5 }],
  },
];

const MOCK_SMART_FIELDS = {
  fields: [
    { id: 'playCount', label: 'Play count', type: 'number', ops: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between'] },
    { id: 'lastPlayed', label: 'Last played', type: 'date', ops: ['inLast', 'notInLast', 'before', 'after', 'never', 'ever'] },
    { id: 'favourite', label: 'Favourite', type: 'bool', ops: ['is', 'isNot'] },
    { id: 'genre', label: 'Genre', type: 'text', ops: ['is', 'isNot', 'contains', 'notContains', 'startsWith'], suggest: 'genres' },
    { id: 'artist', label: 'Artist', type: 'text', ops: ['is', 'isNot', 'contains', 'notContains', 'startsWith'], suggest: 'artists' },
    { id: 'energy', label: 'Energy', type: 'percent', ops: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between'] },
    { id: 'mood', label: 'Mood', type: 'enum', ops: ['is', 'isNot'], options: ['happy', 'sad', 'relaxed', 'aggressive', 'party', 'acoustic'] },
    { id: 'inPlaylist', label: 'In playlist', type: 'playlist', ops: ['is', 'isNot'] },
  ],
  sorts: [{ id: 'random', label: 'Random' }, { id: 'most_played', label: 'Most played' }, { id: 'recently_added', label: 'Recently added' }],
  units: ['days', 'weeks', 'months', 'years'],
  presets: [
    { id: 'recently_added', name: 'Recently added', description: 'Added in the last two weeks.', definition: { match: 'all', rules: [{ field: 'lastPlayed', op: 'inLast', value: 2, unit: 'weeks' }], sort: 'recently_added', limit: { type: 'none' } } },
  ],
};
