import type {
  DataSummary,
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
  Queue,
  ServerInfo,
  Session,
  SmartListKind,
  Suggestion,
  Track,
  TrackInfo,
} from '../types';

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
      else if (this.mode === 'cookie') p = Promise.resolve(`${this.base}/art/${encodeURIComponent(albumId)}?size=${size}`);
      else p = this.blobUrl(`/art/${encodeURIComponent(albumId)}?size=${size}`);
      p.catch(() => this.artCache.delete(key));
      this.artCache.set(key, p);
    }
    return p;
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

  streamBlob(trackId: string, format: 'opus' | 'flac' = 'opus'): Promise<string> {
    return this.blobUrl(`/stream/${encodeURIComponent(trackId)}?format=${format}`);
  }

  suggestionPreviewSrc(id: string): Promise<string> {
    const path = `/suggestions/${encodeURIComponent(id)}/preview`;
    return this.mode === 'cookie' ? Promise.resolve(this.base + path) : this.blobUrl(path);
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
  dataExportUrl = () => `${this.base}/me/data/export`;
  dataExport = () => this.get<unknown>('/me/data/export');
  deleteData = (signal: string) =>
    this.del<{ eventsUpdated?: number; eventsDeleted?: number }>('/me/data', signal === 'all' ? { signal, confirm: true } : { signal });

  // ---- library ----
  artists = (offset = 0, limit = 1000) => this.get<Artist[]>('/library/artists', { offset, limit });
  artist = (id: string) => this.get<Artist & { albums: Album[] }>(`/library/artists/${encodeURIComponent(id)}`);
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
  removeFromPlaylist = (id: string, position: number) =>
    this.del<void>(`/playlists/${encodeURIComponent(id)}/tracks/${position}`);
  movePlaylistTrack = (id: string, from: number, to: number) =>
    this.post<void>(`/playlists/${encodeURIComponent(id)}/move`, { from, to });

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
