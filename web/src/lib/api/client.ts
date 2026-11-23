import type {
  AutoEqEntry,
  AutomixTransition,
  TrackMixDetail,
  AutoEqProfile,
  Feed,
  Blend,
  ReleaseRadar,
  ServerWrapped,
  Wrapped,
  PinType,
  ShelfItem,
  RadioContext,
  RadioSeed,
  RadioStation,
  SteerAction,
  VibeParse,
  SearchResults,
  ToGetItem,
  ToGetStatus,
  DataSummary,
  FamilyMode,
  VoiceDevice,
  VoiceToken,
  AppNotification,
  NewMusic,
  ScrobbleServiceId,
  ScrobbleStatus,
  AdminUser,
  Album,
  Artist,
  DeviceKind,
  FolderListing,
  Genre,
  Invite,
  Lyrics,
  LyricsSource,
  Me,
  Mix,
  MyDevice,
  PlayEvent,
  Playlist,
  PlaylistActivity,
  PlaylistMember,
  Queue,
  ServerInfo,
  Session,
  SmartListKind,
  Suggestion,
  Track,
  TrackInfo,
  ArtistInfo,
  ArtistPage,
  SmartDefinition,
  SmartFields,
  SmartPlaylist,
  SmartPreview,
} from '../types';
import type { JamSettings, JamSnapshot } from '../jam';

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail || `HTTP ${status}`);
  }
}

/**
 * How the client authenticates.
 * - `cookie`: same-origin web player; the server set the httpOnly `nm_session` cookie at login.
 * - `bearer`: cross-origin (desktop app, or a web player pointed at another server); token kept in storage.
 */
export type AuthMode = 'cookie' | 'bearer';

export interface ClientOptions {
  /** Server origin, e.g. `https://music.example.com` or `` (empty = same origin). */
  origin: string;
  mode: AuthMode;
  token?: string | null;
  fetchImpl?: typeof fetch;
  /** Override for media URLs (mock mode serves generated blobs). */
  media?: MediaResolver;
  onUnauthorized?: () => void;
}

export interface MediaResolver {
  art(albumId: string, size: number): Promise<string>;
  stream(trackId: string, format: 'opus' | 'flac'): Promise<string>;
  suggestionCover?(id: string, size: number): Promise<string>;
  catalogCover?(id: string, size: number): Promise<string>;
  catalogPreview?(id: string): Promise<string>;
}

/** Accepts `192.168.68.2:8443`, `music.example.com`, `https://…/`, and returns a clean origin. */
export function normaliseServerAddress(input: string): string {
  let s = input.trim();
  if (!s) return '';
  if (s === 'mock') return 'mock';
  if (!/^[a-z]+:\/\//i.test(s)) s = 'https://' + s;
  const u = new URL(s);
  return `${u.protocol}//${u.host}`;
}

export function isSameOrigin(origin: string, here: string = globalThis.location?.origin ?? ''): boolean {
  return origin === '' || origin === here;
}

export class ApiClient {
  readonly origin: string;
  readonly mode: AuthMode;
  token: string | null;
  private fetchImpl: typeof fetch;
  private media?: MediaResolver;
  private artCache = new Map<string, Promise<string>>();
  onUnauthorized?: () => void;

  constructor(opts: ClientOptions) {
    this.origin = opts.origin;
    this.mode = opts.mode;
    this.token = opts.token ?? null;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.media = opts.media;
    this.onUnauthorized = opts.onUnauthorized;
  }

  get base(): string {
    return `${this.origin}/api/v1`;
  }

  /** WebSocket URL for Connect. Browsers can't set headers, so bearer mode uses `?token=`. */
  get connectUrl(): string {
    const httpBase = this.origin || globalThis.location.origin;
    const ws = httpBase.replace(/^http/, 'ws') + '/api/v1/connect';
    return this.mode === 'bearer' && this.token ? `${ws}?token=${encodeURIComponent(this.token)}` : ws;
  }

  private headers(json: boolean): Record<string, string> {
    const h: Record<string, string> = { Accept: 'application/json' };
    if (json) h['Content-Type'] = 'application/json';
    if (this.mode === 'bearer' && this.token) h.Authorization = `Bearer ${this.token}`;
    return h;
  }

  async request<T>(method: string, path: string, body?: unknown, query?: Record<string, unknown>): Promise<T> {
    let url = this.base + path;
    if (query) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null) q.set(k, String(v));
      const qs = q.toString();
      if (qs) url += (url.includes('?') ? '&' : '?') + qs;
    }
    const res = await this.fetchImpl(url, {
      method,
      headers: this.headers(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: this.mode === 'cookie' ? 'include' : 'omit',
    });
    if (res.status === 401) this.onUnauthorized?.();
    if (!res.ok) {
      let detail: string;
      try {
        const j = await res.json();
        detail = typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? j);
      } catch {
        detail = res.statusText;
      }
      throw new ApiError(res.status, detail);
    }
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  /** fetch() with this client's transport (uploads send raw chunks with their own headers). */
  async fetchRaw(url: string, init: RequestInit): Promise<Response> {
    const res = await this.fetchImpl(url, init);
    if (res.status === 401) this.onUnauthorized?.();
    return res;
  }

  /** PUT a raw body (e.g. an image) and parse the JSON reply. */
  async putRaw<T>(path: string, body: Blob, contentType: string): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json', 'Content-Type': contentType };
    if (this.mode === 'bearer' && this.token) headers.Authorization = `Bearer ${this.token}`;
    const res = await this.fetchImpl(this.base + path, { method: 'PUT', headers, body, credentials: this.mode === 'cookie' ? 'include' : 'omit' });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        if (typeof j?.detail === 'string') detail = j.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  }

  /** Drop cached art URLs for an album (after a cover change); `bust` makes new URLs skip the browser cache. */
  forgetArt(albumId: string) {
    for (const k of [...this.artCache.keys()]) if (k.startsWith(`${albumId}@`)) this.artCache.delete(k);
    this.artBust.set(albumId, Date.now());
  }
  private artBust = new Map<string, number>();

  get = <T>(p: string, q?: Record<string, unknown>) => this.request<T>('GET', p, undefined, q);
  post = <T>(p: string, b: unknown = {}, q?: Record<string, unknown>) => this.request<T>('POST', p, b, q);
  put = <T>(p: string, b: unknown = {}) => this.request<T>('PUT', p, b);
  patch = <T>(p: string, b: unknown = {}) => this.request<T>('PATCH', p, b);
  del = <T>(p: string, q?: Record<string, unknown>) => this.request<T>('DELETE', p, undefined, q);

  // ---- media ----

  /** Image URL usable in <img src>. In bearer mode, fetched with the header into a blob URL (cached). */
  artSrc(albumId: string, size = 256): Promise<string> {
    const key = `${albumId}@${size}`;
    let p = this.artCache.get(key);
    if (!p) {
      if (this.media) p = this.media.art(albumId, size);
      else if (this.mode === 'cookie') p = Promise.resolve(`${this.base}/art/${encodeURIComponent(albumId)}?size=${size}${this.bustQ(albumId)}`);
      else if (this.token) p = Promise.resolve(`${this.base}/art/${encodeURIComponent(albumId)}?size=${size}&token=${encodeURIComponent(this.token)}${this.bustQ(albumId)}`);
      else p = this.blobUrl(`/art/${encodeURIComponent(albumId)}?size=${size}`);
      p.catch(() => this.artCache.delete(key));
      this.artCache.set(key, p);
    }
    return p;
  }

  private bustQ(albumId: string): string {
    const v = this.artBust.get(albumId);
    return v ? `&v=${v}` : '';
  }

  suggestionCoverSrc(id: string, size = 256): Promise<string> {
    if (this.media?.suggestionCover) return this.media.suggestionCover(id, size);
    const path = `/suggestions/${encodeURIComponent(id)}/cover?size=${size}`;
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrl(path);
  }

  /**
   * Audio URL for <audio src>. Cookie mode: plain URL. Bearer mode: `?token=` (needs server support — see
   * README "contract gaps"); `streamBlob` is the fallback when that fails.
   */
  async streamSrc(trackId: string, format: 'opus' | 'flac' = 'opus'): Promise<string> {
    if (this.media) return this.media.stream(trackId, format);
    const url = `${this.base}/stream/${encodeURIComponent(trackId)}?format=${format}`;
    if (this.mode === 'bearer' && this.token) return `${url}&token=${encodeURIComponent(this.token)}`;
    return url;
  }

  /**
   * URL for spoken-word media and covers (`/stream/episode|book|station/…`, `/art/podcast|book|station/…`),
   * usable in <audio>/<img>: same-origin cookie, or `?token=` in bearer mode. Mock mode: '' (no media).
   */
  mediaUrl(path: string): string {
    if (this.media) return '';
    const url = this.base + path;
    if (this.mode === 'bearer' && this.token) return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(this.token)}`;
    return url;
  }

  streamBlob(trackId: string, format: 'opus' | 'flac' = 'opus'): Promise<string> {
    return this.blobUrl(`/stream/${encodeURIComponent(trackId)}?format=${format}`);
  }

  suggestionPreviewSrc(id: string): Promise<string> {
    const path = `/suggestions/${encodeURIComponent(id)}/preview`;
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrl(path);
  }

  /** A URL for a file download: plain in cookie mode, a blob fetched with the header in bearer mode. */
  downloadUrl(path: string): Promise<string> {
    if (this.media && /\/admin\/backups\//.test(path)) return Promise.resolve(URL.createObjectURL(new Blob(['mock backup'], { type: 'application/gzip' })));
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrl(path);
  }

  blobUrlPublic(path: string) {
    return this.blobUrl(path);
  }

  private async blobUrl(path: string): Promise<string> {
    const res = await this.fetchImpl(this.base + path, { headers: this.headers(false), credentials: 'omit' });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return URL.createObjectURL(await res.blob());
  }

  // ---- public ----
  health = () => this.get<{ status: string; serverId: string; version: string }>('/health');
  serverInfo = () => this.get<ServerInfo>('/server-info');
  login = (b: { username: string; password: string; deviceName: string; deviceKind: DeviceKind; cookie?: boolean }) =>
    this.post<Session>('/auth/login', b);
  register = (b: {
    inviteCode: string;
    username: string;
    password: string;
    deviceName: string;
    deviceKind: DeviceKind;
    cookie?: boolean;
  }) => this.post<Session>('/auth/register', b);

  // ---- me ----
  logout = () => this.post<void>('/auth/logout');
  me = () => this.get<Me>('/me');
  changePassword = (current: string, next: string) => this.put<void>('/me/password', { current, new: next });
  myDevices = () => this.get<MyDevice[]>('/me/devices');
  removeDevice = (id: string) => this.del<void>(`/me/devices/${encodeURIComponent(id)}`);
  pairCode = () => this.post<{ pairCode: string; expiresAt?: string; qrPayload: unknown }>('/me/pair-code');
  getSettings = () => this.get<{ settings: Record<string, unknown> }>('/me/settings');
  /** Merge keys (null deletes). */
  putSettings = (b: { settings: Record<string, unknown> }) => this.put<{ settings: Record<string, unknown> }>('/me/settings', b);
  dataSummary = () => this.get<DataSummary>('/me/data');
  /** v6 family mode (locked: loosening needs the admin-set PIN). */
  family = () => this.get<FamilyMode>('/me/family');
  putFamily = (b: Partial<Pick<FamilyMode, 'enabled' | 'hideProbable' | 'hideUnknown'>> & { pin?: string }) =>
    this.put<FamilyMode>('/me/family', b);
  /** v7 voice control: tokens for Home Assistant (POST /voice/command only) and device aliases. */
  voiceTokens = () => this.get<{ tokens: VoiceToken[] }>('/me/voice/tokens');
  createVoiceToken = (name: string) => this.post<VoiceToken & { token: string }>('/me/voice/tokens', { name });
  revokeVoiceToken = (id: string) => this.del<void>(`/me/voice/tokens/${encodeURIComponent(id)}`);
  voiceDevices = () => this.get<{ devices: VoiceDevice[] }>('/me/voice/devices');
  putVoiceDevice = (id: string, b: { aliases?: string[]; isDefault?: boolean }) =>
    this.put<{ id: string; aliases: string[]; isDefault: boolean }>(`/me/voice/devices/${encodeURIComponent(id)}`, b);
  // ---- v7 scrobbling ----
  scrobble = () => this.get<ScrobbleStatus>('/me/scrobble');
  putScrobble = (service: ScrobbleServiceId, b: { enabled?: boolean; nowPlaying?: boolean }) =>
    this.put<ScrobbleStatus>(`/me/scrobble/${service}`, b);
  disconnectScrobble = (service: ScrobbleServiceId) => this.del<ScrobbleStatus>(`/me/scrobble/${service}`);
  connectListenBrainz = (token: string) => this.post<ScrobbleStatus>('/me/scrobble/listenbrainz/connect', { token });
  lastfmConnect = () => this.post<{ authUrl: string }>('/me/scrobble/lastfm/connect');
  /** 409 until the user approved nas-music on last.fm; 410 when the request expired. */
  lastfmComplete = () => this.post<ScrobbleStatus>('/me/scrobble/lastfm/complete');
  retryScrobbles = () => this.post<ScrobbleStatus>('/me/scrobble/retry');
  importListenBrainz = () => this.post<ScrobbleStatus>('/me/scrobble/listenbrainz/import', {});
  removeImported = () => this.del<ScrobbleStatus & { removed: number }>('/me/scrobble/listenbrainz/import');

  // ---- v7 notifications ----
  notifications = (since?: string, limit = 50) =>
    this.get<{ items: AppNotification[]; unread: number }>('/me/notifications', { since, limit });
  readNotifications = (ids?: string[]) => this.post<{ marked: number; unread: number }>('/me/notifications/read', ids ? { ids } : {});
  newMusic = (days = 7) => this.get<NewMusic>('/library/new', { days });
  dataExportUrl = () => `${this.base}/me/data/export`;
  dataExport = () => this.get<unknown>('/me/data/export');
  deleteData = (signal: string) =>
    this.del<{ eventsUpdated?: number; eventsDeleted?: number }>('/me/data', signal === 'all' ? { signal, confirm: true } : { signal });

  // ---- library ----
  artists = (offset = 0, limit = 1000) => this.get<Artist[]>('/library/artists', { offset, limit });
  artist = (id: string) => this.get<Artist & { albums: Album[] }>(`/library/artists/${encodeURIComponent(id)}`);
  /** v7 artist page: photo/bio/links + top songs, albums, appears-on, similar artists. */
  artistPage = (id: string) => this.get<ArtistPage>(`/library/artists/${encodeURIComponent(id)}/page`);
  artistInfo = (id: string) => this.get<ArtistInfo>(`/library/artists/${encodeURIComponent(id)}/info`);
  artistTracks = (id: string) => this.get<Track[]>(`/library/artists/${encodeURIComponent(id)}/tracks`);
  // ---- v7 smart playlists ----
  smartPlaylists = () => this.get<SmartPlaylist[]>('/smart-playlists');
  smartFields = () => this.get<SmartFields>('/smart-playlists/fields');
  smartPlaylist = (id: string) => this.get<SmartPlaylist>(`/smart-playlists/${encodeURIComponent(id)}`);
  createSmartPlaylist = (name: string, definition: SmartDefinition, preset?: string | null) =>
    this.post<SmartPlaylist>('/smart-playlists', { name, definition, preset: preset ?? null });
  updateSmartPlaylist = (id: string, b: { name?: string; definition?: SmartDefinition }) =>
    this.patch<SmartPlaylist>(`/smart-playlists/${encodeURIComponent(id)}`, b);
  deleteSmartPlaylist = (id: string) => this.del<void>(`/smart-playlists/${encodeURIComponent(id)}`);
  previewSmartPlaylist = (definition: SmartDefinition, smartPlaylistId?: string | null, sample = 25) =>
    this.post<SmartPreview>('/smart-playlists/preview', { definition, smartPlaylistId: smartPlaylistId ?? null, sample });
  albums = (sort: 'name' | 'year' | 'added' = 'name', offset = 0, limit = 1000, artistId?: string) =>
    this.get<Album[]>('/library/albums', { sort, offset, limit, artistId });
  album = (id: string) => this.get<Album & { tracks: Track[] }>(`/library/albums/${encodeURIComponent(id)}`);
  track = (id: string) => this.get<Track>(`/library/tracks/${encodeURIComponent(id)}`);
  trackInfo = (id: string) => this.get<TrackInfo>(`/library/tracks/${encodeURIComponent(id)}/info`);
  allTracks = (sort: 'title' | 'added' | 'year' = 'title', offset = 0, limit = 1000) =>
    this.get<Track[]>('/library/tracks', { sort, offset, limit });
  search = (q: string, limit = 50) =>
    this.get<{ artists: Artist[]; albums: Album[]; tracks: Track[] }>('/library/search', { q, limit });
  folders = (path = '') => this.get<FolderListing>('/library/folders', { path });
  genres = () => this.get<(Genre | string)[]>('/library/genres');
  genreTracks = (g: string) => this.get<Track[]>(`/library/genres/${encodeURIComponent(g)}/tracks`);
  smartList = (kind: SmartListKind, offset = 0, limit = 500) => this.get<Track[]>(`/lists/${kind}`, { offset, limit });

  // ---- AutoEq headphone profiles (server-side proxy, fetched on demand) ----
  autoEqSearch = (q: string, limit = 50) => this.get<{ total: number; results: AutoEqEntry[] }>('/eq/autoeq', { q, limit });
  autoEqProfile = (id: string) => this.get<AutoEqProfile>('/eq/autoeq/profile', { id });

  // ---- favourites / lyrics / ai ----
  star = (id: string) => this.put<void>(`/favourites/${encodeURIComponent(id)}`);
  unstar = (id: string) => this.del<void>(`/favourites/${encodeURIComponent(id)}`);
  lyrics = (id: string, source?: LyricsSource) => this.get<Lyrics>(`/tracks/${encodeURIComponent(id)}/lyrics`, { source });
  saveLyrics = (id: string, raw: string) => this.put<Lyrics | void>(`/tracks/${encodeURIComponent(id)}/lyrics`, { raw });
  deleteLyrics = (id: string) => this.del<void>(`/tracks/${encodeURIComponent(id)}/lyrics`);
  why = (id: string) => this.get<{ reason: string; similarTo?: Track[] }>(`/tracks/${encodeURIComponent(id)}/why`);
  mixesToday = () => this.get<Mix[]>('/mixes/today');
  mix = (id: string) => this.get<Mix>(`/mixes/${encodeURIComponent(id)}`);
  moreLike = (id: string, minutes = 40) => this.post<Mix>(`/mixes/more-like/${encodeURIComponent(id)}`, undefined, { minutes });
  suggestions = () => this.get<Suggestion[]>('/suggestions');
  setSuggestion = (id: string, status: Suggestion['status']) =>
    this.post<void>(`/suggestions/${encodeURIComponent(id)}`, { status });
  sendEvents = (events: PlayEvent[]) => this.post<{ accepted: number; duplicates: number }>('/events', { events });

  // ---- playlists ----
  playlists = () => this.get<Playlist[]>('/playlists');
  createPlaylist = (name: string) => this.post<Playlist>('/playlists', { name });
  playlist = (id: string) => this.get<Playlist>(`/playlists/${encodeURIComponent(id)}`);
  renamePlaylist = (id: string, name: string) => this.patch<Playlist>(`/playlists/${encodeURIComponent(id)}`, { name });
  deletePlaylist = (id: string) => this.del<void>(`/playlists/${encodeURIComponent(id)}`);
  addToPlaylist = (id: string, trackIds: string[], position?: number) =>
    this.post<Playlist | void>(`/playlists/${encodeURIComponent(id)}/tracks`, { trackIds, position });
  /** v5: insert right after an entry ('' = at the top). */
  addToPlaylistAfter = (id: string, trackIds: string[], afterEntryId: string) =>
    this.post<Playlist>(`/playlists/${encodeURIComponent(id)}/tracks`, { trackIds, afterEntryId });
  removePlaylistEntry = (id: string, entryId: string) =>
    this.del<Playlist>(`/playlists/${encodeURIComponent(id)}/entries/${encodeURIComponent(entryId)}`);
  movePlaylistEntry = (id: string, entryId: string, afterEntryId: string | null) =>
    this.post<Playlist>(`/playlists/${encodeURIComponent(id)}/entries/${encodeURIComponent(entryId)}/move`, { afterEntryId });
  playlistMembers = (id: string) => this.get<PlaylistMember[]>(`/playlists/${encodeURIComponent(id)}/members`);
  addPlaylistMember = (id: string, b: { userId?: string; username?: string; role: 'editor' | 'viewer' }) =>
    this.post<PlaylistMember[]>(`/playlists/${encodeURIComponent(id)}/members`, b);
  setPlaylistMemberRole = (id: string, userId: string, role: 'editor' | 'viewer') =>
    this.patch<PlaylistMember[]>(`/playlists/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`, { role });
  removePlaylistMember = (id: string, userId: string) =>
    this.del<void>(`/playlists/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`);
  playlistActivity = (id: string, limit = 100) =>
    this.get<PlaylistActivity[]>(`/playlists/${encodeURIComponent(id)}/activity?limit=${limit}`);
  userDirectory = () => this.get<{ id: string; username: string }[]>('/users');
  removeFromPlaylist = (id: string, position: number) =>
    this.del<void>(`/playlists/${encodeURIComponent(id)}/tracks/${position}`);
  movePlaylistTrack = (id: string, from: number, to: number) =>
    this.post<void>(`/playlists/${encodeURIComponent(id)}/move`, { from, to });

  // ---- Jam (listen together; host side) ----
  startJam = (b: Partial<JamSettings> & { linkTtlMinutes?: number } = {}) => this.post<JamSnapshot>('/jams', b);
  currentJam = () => this.get<JamSnapshot | undefined>('/jams/current');
  jam = (id: string) => this.get<JamSnapshot>(`/jams/${encodeURIComponent(id)}`);
  patchJam = (id: string, b: Partial<JamSettings>) => this.patch<JamSnapshot>(`/jams/${encodeURIComponent(id)}`, b);
  endJam = (id: string) => this.del<void>(`/jams/${encodeURIComponent(id)}`);
  jamLink = (id: string, b: { ttlMinutes?: number; close?: boolean } = {}) =>
    this.post<JamSnapshot>(`/jams/${encodeURIComponent(id)}/link`, b);
  jamQr = (id: string) =>
    this.get<{ joinUrl: string | null; lanJoinUrl: string | null; qr: string | null; lanQr: string | null }>(`/jams/${encodeURIComponent(id)}/qr`);
  decideJamRequest = (id: string, requestId: string, approve: boolean) =>
    this.post<JamSnapshot>(`/jams/${encodeURIComponent(id)}/requests/${encodeURIComponent(requestId)}`, { approve });
  removeJamEntry = (id: string, position: number, trackId?: string) =>
    this.del<JamSnapshot>(`/jams/${encodeURIComponent(id)}/queue/${position}`, trackId ? { trackId } : undefined);
  kickJamGuest = (id: string, guestId: string) =>
    this.del<JamSnapshot>(`/jams/${encodeURIComponent(id)}/guests/${encodeURIComponent(guestId)}`);
  /** Host's live Jam updates. Browsers can't set headers, so bearer mode uses `?token=`. */
  jamWsUrl(id: string): string {
    const httpBase = this.origin || globalThis.location.origin;
    const ws = `${httpBase.replace(/^http/, 'ws')}/api/v1/jam/ws?jam=${encodeURIComponent(id)}`;
    return this.mode === 'bearer' && this.token ? `${ws}&token=${encodeURIComponent(this.token)}` : ws;
  }

  // ---- queues ----
  queues = () => this.get<Queue[]>('/queues');
  createQueue = (name: string, trackIds: string[], startIndex = 0) =>
    this.post<Queue>('/queues', { name, trackIds, startIndex });
  queue = (id: string) => this.get<Queue>(`/queues/${encodeURIComponent(id)}`);
  patchQueue = (id: string, b: Partial<Pick<Queue, 'name' | 'currentIndex' | 'positionMs'>>) =>
    this.patch<Queue>(`/queues/${encodeURIComponent(id)}`, b);
  deleteQueue = (id: string) => this.del<void>(`/queues/${encodeURIComponent(id)}`);
  addToQueue = (id: string, trackIds: string[], position: number | 'next' | 'end') =>
    this.post<Queue | void>(`/queues/${encodeURIComponent(id)}/tracks`, { trackIds, position });
  removeFromQueue = (id: string, position: number) => this.del<void>(`/queues/${encodeURIComponent(id)}/tracks/${position}`);
  moveQueueTrack = (id: string, from: number, to: number) =>
    this.post<void>(`/queues/${encodeURIComponent(id)}/move`, { from, to });
  saveQueueAsPlaylist = (id: string, name: string) =>
    this.post<Playlist>(`/queues/${encodeURIComponent(id)}/save-as-playlist`, { name });

  // ---- v3: feeds, global search, To get, pins, radio ----
  home = () => this.get<Feed>('/home');
  forYou = () => this.get<Feed>('/foryou');
  globalSearch = (q: string, limit = 20, scope: 'all' | 'library' | 'catalog' = 'all') => this.get<SearchResults>('/search', { q, limit, scope });

  /** Image for a relative `artUrl` ("art/<id>", "catalog/<id>/cover", "suggestions/<id>/cover"). */
  relArtSrc(rel: string, size = 256): Promise<string> {
    const clean = rel.replace(/^\/?(api\/v1\/)?/, '').split('?')[0];
    let m = /^art\/([^/]+)$/.exec(clean);
    if (m) return this.artSrc(decodeURIComponent(m[1]), size);
    m = /^catalog\/([^/]+)\/cover$/.exec(clean);
    if (m) return this.catalogCoverSrc(decodeURIComponent(m[1]), size);
    m = /^suggestions\/([^/]+)\/cover$/.exec(clean);
    if (m) return this.suggestionCoverSrc(decodeURIComponent(m[1]), size);
    const path = `/${clean}?size=${size}`;
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrlPublic(path);
  }
  catalogPreviewSrc = (id: string): Promise<string> => {
    const path = `/catalog/${encodeURIComponent(id)}/preview`;
    if (this.media?.catalogPreview) return this.media.catalogPreview(id);
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrlPublic(path);
  };
  catalogCoverSrc = (id: string, size = 256): Promise<string> => {
    const path = `/catalog/${encodeURIComponent(id)}/cover?size=${size}`;
    if (this.media?.catalogCover) return this.media.catalogCover(id, size);
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrlPublic(path);
  };
  toGet = (status: ToGetStatus | 'all' = 'all') => this.get<ToGetItem[]>('/toget', { status });
  addToGet = (b: { catalogId?: string; type?: 'album' | 'track'; title?: string; artist?: string | null; year?: number | null; mbid?: string | null; note?: string }) =>
    this.post<ToGetItem>('/toget', b);
  patchToGet = (id: string, b: { status?: ToGetStatus; note?: string | null }) => this.patch<ToGetItem>(`/toget/${encodeURIComponent(id)}`, b);
  deleteToGet = (id: string) => this.del<void>(`/toget/${encodeURIComponent(id)}`);
  pins = () => this.get<ShelfItem[]>('/pins');
  pin = (type: PinType, id: string, position?: number) => this.post<ShelfItem[]>('/pins', { type, id, position });
  unpin = (type: PinType, id: string) => this.del<void>(`/pins/${type}/${encodeURIComponent(id)}`);
  /** api 0.8 Continue listening: music queues left part-way + episodes/books in progress, newest first. */
  continueListening = (limit = 20) => this.get<{ items: ShelfItem[] }>('/continue', { limit }).then((r) => r.items);
  dismissContinue = (type: 'queue' | 'episode' | 'book', id: string) => this.del<void>(`/continue/${type}/${encodeURIComponent(id)}`);
  startRadio = (seed: RadioSeed, context?: RadioContext, automix = false) =>
    this.post<Queue>('/radio', { seed, ...(context ? { context } : {}), ...(automix ? { automix } : {}) });
  // ---- AI radio (v6) ----
  parseVibe = (text: string) => this.post<VibeParse>('/radio/vibe/parse', { text });
  radioStation = (queueId: string) => this.get<RadioStation>(`/radio/${encodeURIComponent(queueId)}/station`);
  steerRadio = (queueId: string, body: { action: SteerAction; trackId?: string | null; scope?: 'track' | 'artist'; refId?: string | null; currentIndex?: number | null; refresh?: boolean }) =>
    this.post<{ station: RadioStation; queue: Queue }>(`/radio/${encodeURIComponent(queueId)}/steer`, body);
  radioMore = (queueId: string, count = 25, automix = false) =>
    this.post<Queue>(`/radio/${encodeURIComponent(queueId)}/more`, automix ? { count, automix } : { count });
  // ---- v7: DJ automix ----
  automixOrder = (trackIds: string[], opts: { keepFirst?: boolean; window?: number; arc?: boolean } = {}) =>
    this.post<{ trackIds: string[]; transitions: AutomixTransition[] }>('/automix/order', { trackIds, ...opts });
  trackMix = (trackId: string) => this.get<TrackMixDetail>(`/tracks/${encodeURIComponent(trackId)}/mix`);

  // ---- v5: Release Radar, Wrapped, Blend ----
  releaseRadar = () => this.get<ReleaseRadar>('/release-radar');
  wrapped = (year?: number) => this.get<Wrapped>('/wrapped', year ? { year } : undefined);
  wrappedYears = () => this.get<number[]>('/wrapped/years');
  adminWrapped = (year?: number) => this.get<ServerWrapped>('/admin/wrapped', year ? { year } : undefined);
  blends = () => this.get<Blend[]>('/blends');
  blendCandidates = () => this.get<{ userId: string; username: string }[]>('/blends/candidates');
  createBlend = (usernames: string[], name?: string | null) => this.post<Blend>('/blends', { usernames, name: name || null });
  blend = (id: string) => this.get<Blend>(`/blends/${encodeURIComponent(id)}`);
  renameBlend = (id: string, name: string) => this.patch<Blend>(`/blends/${encodeURIComponent(id)}`, { name });
  inviteToBlend = (id: string, usernames: string[]) => this.post<Blend>(`/blends/${encodeURIComponent(id)}/invite`, { usernames });
  acceptBlend = (id: string) => this.post<Blend>(`/blends/${encodeURIComponent(id)}/accept`);
  declineBlend = (id: string) => this.post<void>(`/blends/${encodeURIComponent(id)}/decline`);
  leaveBlend = (id: string) => this.post<void>(`/blends/${encodeURIComponent(id)}/leave`);
  refreshBlend = (id: string) => this.post<Blend>(`/blends/${encodeURIComponent(id)}/refresh`);

  // ---- admin ----
  invites = () => this.get<Invite[]>('/admin/invites');
  createInvite = (note?: string, expiresInDays = 7) => this.post<Invite>('/admin/invites', { note, expiresInDays });
  deleteInvite = (code: string) => this.del<void>(`/admin/invites/${encodeURIComponent(code)}`);
  users = () => this.get<AdminUser[]>('/admin/users');
  patchUser = (id: string, b: { disabled?: boolean; isAdmin?: boolean }) =>
    this.patch<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, b);
  resetPassword = (id: string, pw: string) =>
    this.post<void>(`/admin/users/${encodeURIComponent(id)}/reset-password`, { new: pw });
}
