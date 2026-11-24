// Admin console API (/api/v1/admin/*). Shapes are this client's proposal until api/openapi.yaml defines
// them; everything admin goes through this one module so adopting the real contract touches one file.
import type { ApiClient } from './client';
import type { DeviceKind, ExplicitStatus, FamilyMode, MyDevice, ToGetItem, Track } from '../types';

export interface DiskUsage {
  name: string;
  path: string;
  usedBytes: number;
  totalBytes: number;
}

export type JobName = 'scan' | 'analyse' | 'loudness' | 'nightly' | 'discovery' | 'pretranscode' | 'backup' | 'autotag';

export interface JobStatus {
  name: JobName | string;
  state: 'idle' | 'running' | 'failed';
  startedAt?: string | null;
  finishedAt?: string | null;
  ok?: boolean | null;
  durationS?: number | null;
  error?: string | null;
  result?: unknown;
  nextRunAt?: string | null;
}

export interface ServerStatus {
  version: string;
  uptimeS: number;
  cpuPct: number;
  load?: number[];
  memUsedBytes: number;
  memTotalBytes: number;
  disks: DiskUsage[];
  dataBytes: number;
  library: { tracks: number; albums: number; artists: number; missing: number };
  analysis: { done: number; total: number; failed: number; ratePerHour?: number | null; etaS?: number | null; modelVersion?: string | null };
  jobs: JobStatus[];
}

export interface NowPlayingRow {
  userId: string;
  username: string;
  deviceId: string;
  deviceName: string;
  kind: DeviceKind;
  playing: boolean;
  positionMs: number;
  track: Track | null;
}

export interface FailedFile {
  id: string;
  path: string;
  stage: 'scan' | 'analyse' | 'transcode';
  error: string;
  attempts: number;
  lastTriedAt?: string | null;
}

export type HealthCheckId = 'missing' | 'duplicates' | 'missing_tags' | 'missing_art' | 'unanalysed' | 'orphan_cache' | 'old_backups';

export interface HealthCheck {
  id: HealthCheckId | string;
  title: string;
  description?: string;
  count: number;
  bytes?: number | null;
  /** The server can fix/remove these; the client always confirms first. */
  fix?: { label: string; destructive: boolean } | null;
}

export interface HealthItem {
  id: string;
  title: string;
  detail?: string | null;
  path?: string | null;
  bytes?: number | null;
  /** duplicates: the other copies */
  group?: string | null;
}

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';

export interface LogEntry {
  seq: number;
  at: string;
  level: LogLevel | string;
  logger: string;
  message: string;
}

export interface JobRun {
  id: string;
  name: string;
  startedAt: string;
  finishedAt?: string | null;
  ok?: boolean | null;
  durationS?: number | null;
  error?: string | null;
  trigger?: 'schedule' | 'manual' | 'startup' | string;
  summary?: string | null;
}

export interface BugReport {
  id: string;
  createdAt: string;
  username?: string | null;
  deviceName?: string | null;
  kind?: DeviceKind | string;
  appVersion?: string | null;
  title: string;
  status: 'open' | 'closed';
}

export interface BugReportDetail extends BugReport {
  description: string;
  logs?: string | null;
  context?: Record<string, unknown> | null;
}

export interface Backup {
  id: string;
  createdAt: string;
  bytes: number;
  kind?: 'manual' | 'scheduled' | string;
  note?: string | null;
}

export interface AdminSettings {
  lanUrl: string | null;
  tunnelUrl: string | null;
  timezone: string;
  opusCacheMaxGb: number;
  lrclib: boolean;
}

export interface SettingsSaveResult {
  values: AdminSettings;
  /** Keys whose new value only applies after a restart. */
  restartRequired: string[];
}

// ---- v5 tag editing (/admin/tags) ----

export interface TagValues {
  title?: string | null;
  artist?: string | null;
  albumArtist?: string | null;
  album?: string | null;
  trackNo?: number | null;
  discNo?: number | null;
  year?: number | null;
  genre?: string | null;
  lyrics?: string | null;
  /** v6 admin verdict (null = automatic). */
  explicit?: 'explicit' | 'clean' | null;
}
export type TagField = keyof TagValues | 'cover';

export interface TrackTagDetail {
  trackId: string;
  path: string;
  albumId: string;
  file: TagValues;
  effective: TagValues;
  overridden: TagField[];
  lyrics: string | null;
  hasCoverOverride: boolean;
}

export interface TagEditResult {
  batchId: string;
  changed: number;
  albumId?: string;
}

export interface TagEditLogEntry {
  id: number;
  batchId: string;
  trackId: string;
  trackTitle: string;
  path: string;
  at: string;
  user: string | null;
  action: 'set' | 'revert';
  field: string;
  oldValue: unknown;
  newValue: unknown;
}

export interface TagSuggestion {
  score?: number | null;
  recordingMbid?: string | null;
  releaseMbid?: string | null;
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  albumArtist?: string | null;
  year?: number | null;
  trackNo?: number | null;
  discNo?: number | null;
  country?: string | null;
  trackCount?: number | null;
  format?: string | null;
}

export interface MbRelease {
  releaseMbid: string;
  album: string;
  albumArtist: string | null;
  year: number | null;
  genre: string | null;
  discCount: number;
  tracks: { discNo: number; trackNo: number; title: string; artist: string | null; recordingMbid: string }[];
}

export function tagsApi(c: ApiClient) {
  const enc = encodeURIComponent;
  return {
    track: (id: string) => c.get<TrackTagDetail>(`/admin/tags/tracks/${enc(id)}`),
    edit: (trackIds: string[], set: TagValues = {}, revert: string[] = []) =>
      c.post<TagEditResult>('/admin/tags/edit', { trackIds, set, revert }),
    editAlbum: (albumId: string, set: TagValues = {}, revert: string[] = []) =>
      c.post<TagEditResult>(`/admin/tags/albums/${enc(albumId)}`, { set, revert }),
    uploadCover: (albumId: string, file: Blob) =>
      c.putRaw<TagEditResult>(`/admin/tags/albums/${enc(albumId)}/cover`, file, file.type || 'application/octet-stream'),
    revertCover: (albumId: string) => c.del<TagEditResult>(`/admin/tags/albums/${enc(albumId)}/cover`),
    history: (q: { trackId?: string; albumId?: string; limit?: number; offset?: number } = {}) =>
      c.get<TagEditLogEntry[]>('/admin/tags/history', q),
    musicbrainz: (q: { trackId?: string; albumId?: string; q?: string; artist?: string }) =>
      c.get<{ kind: 'recordings' | 'releases'; results: TagSuggestion[] }>('/admin/tags/musicbrainz', q),
    mbRelease: (mbid: string) => c.get<MbRelease>(`/admin/tags/musicbrainz/release/${enc(mbid)}`),
  };
}

export function adminApi(c: ApiClient) {
  const enc = encodeURIComponent;
  return {
    status: () => c.get<ServerStatus>('/admin/status'),
    nowPlaying: () => c.get<NowPlayingRow[]>('/admin/now-playing'),
    runJob: (name: JobName) => c.post<JobStatus>(`/admin/jobs/${enc(name)}/run`),
    jobHistory: (limit = 50) => c.get<JobRun[]>('/admin/jobs/history', { limit }),

    userDevices: (id: string) => c.get<MyDevice[]>(`/admin/users/${enc(id)}/devices`),

    failed: () => c.get<FailedFile[]>('/admin/library/failed'),
    retryFailed: (id?: string) => c.post<{ queued: number }>(id ? `/admin/library/failed/${enc(id)}/retry` : '/admin/library/failed/retry'),

    health: () => c.get<HealthCheck[]>('/admin/health'),
    healthItems: (id: string, limit = 200) => c.get<HealthItem[]>(`/admin/health/${enc(id)}`, { limit }),
    healthFix: (id: string, itemIds?: string[]) =>
      c.post<{ fixed: number; freedBytes?: number | null }>(`/admin/health/${enc(id)}/fix`, itemIds ? { ids: itemIds } : {}),

    logs: (q: { after?: number; level?: string; q?: string; limit?: number }) => c.get<LogEntry[]>('/admin/logs', q),

    reports: () => c.get<BugReport[]>('/admin/reports'),
    report: (id: string) => c.get<BugReportDetail>(`/admin/reports/${enc(id)}`),
    setReportStatus: (id: string, status: BugReport['status']) => c.patch<BugReport>(`/admin/reports/${enc(id)}`, { status }),
    deleteReport: (id: string) => c.del<void>(`/admin/reports/${enc(id)}`),

    backups: () => c.get<Backup[]>('/admin/backups'),
    createBackup: (note?: string) => c.post<Backup>('/admin/backups', { note }),
    deleteBackup: (id: string) => c.del<void>(`/admin/backups/${enc(id)}`),
    downloadBackup: (id: string) => c.downloadUrl(`/admin/backups/${enc(id)}/download`),

    wants: (q: WantQuery = {}) => c.get<AdminWants>('/admin/wants', { ...q }),
    checkWant: (id: string, note?: string | null) => c.put<AdminWantItem>(`/admin/wants/${enc(id)}/done`, { note: note ?? null }),
    uncheckWant: (id: string) => c.del<AdminWantItem>(`/admin/wants/${enc(id)}/done`),
    checkFoundWants: (ids?: string[]) => c.post<{ checked: number }>('/admin/wants/check-found', ids ? { ids } : {}),

    settings: () => c.get<AdminSettings>('/admin/settings'),
    saveSettings: (s: Partial<AdminSettings>) => c.put<SettingsSaveResult>('/admin/settings', s),
  };
}

export type AdminApi = ReturnType<typeof adminApi>;

// ---- want list of every user (schema v8) ----

export interface AdminWantItem extends ToGetItem {
  userId: string;
  username: string;
  /** checked off by an admin ("added") */
  done: boolean;
  doneAt?: string | null;
  doneNote?: string | null;
  doneBy?: string | null;
  doneAuto?: boolean;
  /** not checked yet, but found in the library */
  suggestDone: boolean;
}

export interface AdminWantUser {
  userId: string;
  username: string;
  open: number;
  done: number;
  found: number;
  total: number;
}

export interface AdminWants {
  items: AdminWantItem[];
  users: AdminWantUser[];
}

export interface WantQuery {
  userId?: string;
  type?: 'album' | 'track';
  status?: 'active' | 'want' | 'got' | 'dismissed' | 'all';
  done?: boolean;
  q?: string;
}

/** Client-side filter, same rules as the server's (used for instant search while typing). */
export function filterWants(items: AdminWantItem[], f: { userId?: string; type?: string; hideDone?: boolean; q?: string }): AdminWantItem[] {
  const q = (f.q ?? '').trim().toLowerCase();
  return items.filter(
    (i) =>
      (!f.userId || i.userId === f.userId) &&
      (!f.type || i.type === f.type) &&
      (!f.hideDone || !i.done) &&
      (!q || [i.title, i.artist, i.note ?? '', i.username].some((s) => s.toLowerCase().includes(q))),
  );
}

// ---- validation for the Settings section ----

/** A public base URL: http(s), host, optional port, no path/query. Returns the cleaned URL or an error. */
export function validateBaseUrl(v: string, required = false): { ok: true; value: string | null } | { ok: false; error: string } {
  const s = v.trim();
  if (!s) return required ? { ok: false, error: 'Required' } : { ok: true, value: null };
  let u: URL;
  try {
    u = new URL(/^[a-z]+:\/\//i.test(s) ? s : `https://${s}`);
  } catch {
    return { ok: false, error: 'Not a valid URL' };
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return { ok: false, error: 'Must start with https:// or http://' };
  if (!u.hostname || /\s/.test(s)) return { ok: false, error: 'Missing host name' };
  if (u.port && (Number(u.port) < 1 || Number(u.port) > 65535)) return { ok: false, error: 'Port must be 1–65535' };
  if ((u.pathname && u.pathname !== '/') || u.search || u.hash) return { ok: false, error: 'Only scheme, host and port — no path' };
  if (u.username || u.password) return { ok: false, error: 'No user name or password in the URL' };
  return { ok: true, value: `${u.protocol}//${u.host}` };
}

export function validateTimezone(tz: string, known?: string[]): string | null {
  if (!tz.trim()) return 'Required';
  if (known?.length) return known.includes(tz) ? null : 'Unknown time zone';
  try {
    new Intl.DateTimeFormat('en', { timeZone: tz });
    return null;
  } catch {
    return 'Unknown time zone';
  }
}

export function validateCacheGb(v: number): string | null {
  if (!Number.isFinite(v)) return 'Enter a number';
  if (v < 1) return 'At least 1 GB';
  if (v > 2000) return 'At most 2000 GB';
  return null;
}

/** ETA text for analysis progress. */
export function etaText(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return '—';
  if (s < 90) return 'about a minute';
  const m = Math.round(s / 60);
  if (m < 90) return `about ${m} min`;
  const h = Math.round(m / 6) / 10;
  if (h < 48) return `about ${h} h`;
  return `about ${Math.round(h / 24)} days`;
}

export function uptimeText(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

/** Client-side log filter (also applied to what the live tail already holds). */
export function filterLogs(entries: LogEntry[], minLevel: string, q: string): LogEntry[] {
  const order = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
  const min = Math.max(0, order.indexOf(minLevel));
  const needle = q.trim().toLowerCase();
  return entries.filter(
    (e) => Math.max(0, order.indexOf(e.level)) >= min && (!needle || e.message.toLowerCase().includes(needle) || e.logger.toLowerCase().includes(needle)),
  );
}

// ---- v6 explicit flags + family mode (/admin/explicit, /admin/family) ----

export type ExplicitSource = 'admin' | 'tags' | 'deezer' | 'lyrics';

export interface ExplicitEvidence {
  status: ExplicitStatus;
  source: ExplicitSource | null;
  admin: 'explicit' | 'clean' | null;
  tags: 'explicit' | 'clean' | null;
  deezer: 'explicit' | 'clean' | 'not_found' | null;
  deezerId?: number | null;
  lyrics: 'probable' | 'clean' | 'none' | null;
  lyricsHits: number | null;
  updatedAt?: string | null;
}

export interface ExplicitReviewItem extends ExplicitEvidence {
  trackId: string;
  title: string;
  artist: string;
  album: string;
  albumId: string;
}

export interface ExplicitReview {
  total: number;
  counts: Record<ExplicitStatus, number>;
  items: ExplicitReviewItem[];
}

export interface AdminFamilyUser extends FamilyMode {
  userId: string;
  username: string;
  isAdmin: boolean;
  lockedBy: string | null;
}

export function familyApi(c: ApiClient) {
  const enc = encodeURIComponent;
  return {
    review: (p: { status?: ExplicitStatus[]; source?: ExplicitSource | 'none'; q?: string; limit?: number; offset?: number } = {}) => {
      const qs = new URLSearchParams();
      for (const s of p.status ?? []) qs.append('status', s);
      if (p.source) qs.set('source', p.source);
      if (p.q) qs.set('q', p.q);
      qs.set('limit', String(p.limit ?? 100));
      qs.set('offset', String(p.offset ?? 0));
      return c.get<ExplicitReview>(`/admin/explicit?${qs}`);
    },
    set: (b: { trackIds?: string[]; albumIds?: string[]; value: 'explicit' | 'clean' | null }) =>
      c.post<TagEditResult & { tracks: number }>('/admin/explicit', b),
    users: () => c.get<AdminFamilyUser[]>('/admin/family'),
    setUser: (
      userId: string,
      b: Partial<Pick<FamilyMode, 'enabled' | 'hideProbable' | 'hideUnknown' | 'locked'>> & { pin?: string; clearPin?: boolean },
    ) => c.put<AdminFamilyUser>(`/admin/users/${enc(userId)}/family`, b),
  };
}

// ---- automatic tag fixing + duplicates (schema v6, api/openapi.yaml 0.6.0) ----

export interface AutotagValues extends TagValues {
  mbRecordingId?: string | null;
  mbReleaseGroupId?: string | null;
  mbArtistId?: string | null;
  mbAlbumArtistId?: string | null;
}
export type AutotagField = Exclude<keyof AutotagValues, 'lyrics'>;
export type AutotagStatusName = 'pending' | 'accepted' | 'rejected';

export interface AutotagStatus {
  apiKeyConfigured: boolean;
  fpcalcAvailable: boolean;
  running: boolean;
  tracks: number;
  fingerprinted: number;
  fingerprintFailed: number;
  matched: number;
  noMatch: number;
  lookupErrors: number;
  pending: number;
  accepted: number;
  rejected: number;
}

export interface AutotagSuggestion {
  trackId: string;
  path: string;
  status: AutotagStatusName;
  confidence: number;
  current: AutotagValues;
  suggested: AutotagValues;
  changes: AutotagField[];
  defaultFields: AutotagField[];
  recordingMbid?: string | null;
  releaseMbid?: string | null;
  durationS?: number;
  detail?: Record<string, unknown>;
  decidedAt?: string | null;
  batchId?: string | null;
}

export interface AutotagGroup {
  groupId: string;
  albumId: string;
  currentAlbum: string | null;
  currentAlbumArtist: string | null;
  albumHasArt: boolean;
  confidence: number;
  albumTrackCount: number;
  release: {
    mbid: string;
    title: string | null;
    artist: string | null;
    year: number | null;
    country: string | null;
    format: string | null;
    trackCount: number | null;
    type: string | null;
    hasCover: boolean;
  } | null;
  tracks: AutotagSuggestion[];
}

export interface AutotagAcceptBody {
  trackIds?: string[];
  groupIds?: string[];
  minConfidence?: number;
  fields?: AutotagField[];
  edits?: Record<string, AutotagValues>;
  cover?: boolean | null;
}

export interface DuplicateTrack {
  id: string;
  path: string;
  title: string;
  artist: string;
  album: string;
  durationS: number;
  codec: string | null;
  bitrate: number | null;
  sampleRate: number | null;
  bitsPerSample: number | null;
  bytes: number;
  hidden: boolean;
  keptId: string | null;
  recommended: boolean;
  /** v9 fake-lossless checker (lossless codecs; null = not checked yet). */
  losslessVerdict?: LosslessVerdict | null;
  losslessNote?: string | null;
}

export interface DuplicateGroup {
  groupId: string;
  similarity: number | null;
  hiddenCount: number;
  tracks: DuplicateTrack[];
}

export function autotagApi(c: ApiClient) {
  const enc = encodeURIComponent;
  return {
    status: () => c.get<AutotagStatus>('/admin/autotag/status'),
    groups: (
      q: {
        status?: AutotagStatusName;
        minConfidence?: number;
        q?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) => c.get<{ total: number; groups: AutotagGroup[] }>('/admin/autotag/groups', q),
    accept: (body: AutotagAcceptBody) =>
      c.post<{
        accepted: number;
        batchId: string | null;
        changed: number;
        coverErrors: string[];
      }>('/admin/autotag/accept', body),
    reject: (body: { trackIds?: string[]; groupIds?: string[] }) => c.post<{ rejected: number }>('/admin/autotag/reject', body),
    reopen: (trackIds: string[]) =>
      c.post<{ reopened: number; reverted: number }>('/admin/autotag/reopen', {
        trackIds,
      }),
    rematch: (trackIds: string[]) => c.post<{ queued: number }>('/admin/autotag/rematch', { trackIds }),
    coverPath: (releaseMbid: string) => `/admin/autotag/cover/${enc(releaseMbid)}`,
    duplicates: () => c.get<DuplicateGroup[]>('/admin/duplicates'),
    resolve: (keepTrackId: string, hideTrackIds: string[], remove = false) =>
      c.post<{ hidden: number; keptId: string; removed?: number; batchId?: string; purgeAfter?: string }>('/admin/duplicates/resolve', {
        keepTrackId,
        hideTrackIds,
        remove,
      }),
    unhide: (trackIds: string[]) => c.post<{ unhidden: number }>('/admin/duplicates/unhide', { trackIds }),
  };
}

export const AUTOTAG_FIELDS: {
  key: AutotagField;
  label: string;
  mb?: boolean;
}[] = [
  { key: 'title', label: 'Title' },
  { key: 'artist', label: 'Artist' },
  { key: 'albumArtist', label: 'Album artist' },
  { key: 'album', label: 'Album' },
  { key: 'trackNo', label: 'Track' },
  { key: 'discNo', label: 'Disc' },
  { key: 'year', label: 'Year' },
  { key: 'genre', label: 'Genre' },
  { key: 'mbRecordingId', label: 'MB recording', mb: true },
  { key: 'mbReleaseGroupId', label: 'MB release group', mb: true },
  { key: 'mbArtistId', label: 'MB artist', mb: true },
  { key: 'mbAlbumArtistId', label: 'MB album artist', mb: true },
];

/** "87%" for a 0..1 confidence; a CSS class by band. */
export function confidenceText(c: number): string {
  return `${Math.round(Math.max(0, Math.min(1, c)) * 100)}%`;
}
export function confidenceClass(c: number): 'ok' | 'warnc' | 'bad' {
  return c >= 0.85 ? 'ok' : c >= 0.6 ? 'warnc' : 'bad';
}

/** Pending suggestions an "accept all above X%" would take (per-track confidence, like the server). */
export function countAbove(groups: AutotagGroup[], threshold: number): number {
  return groups.reduce((n, g) => n + g.tracks.filter((t) => t.status === 'pending' && t.confidence >= threshold).length, 0);
}

/** Fields that differ, for a group header: the union over its tracks, in display order. */
export function groupChanges(g: AutotagGroup): AutotagField[] {
  const s = new Set(g.tracks.flatMap((t) => t.changes));
  return AUTOTAG_FIELDS.map((f) => f.key).filter((k) => s.has(k));
}

/** Human size for duplicate copies. */
export function qualityText(t: Pick<DuplicateTrack, 'codec' | 'bitrate' | 'sampleRate' | 'bitsPerSample'>): string {
  const parts = [t.codec ?? '?'];
  if (t.bitsPerSample && t.sampleRate) parts.push(`${t.bitsPerSample}-bit/${(t.sampleRate / 1000).toFixed(t.sampleRate % 1000 ? 1 : 0)} kHz`);
  else if (t.sampleRate) parts.push(`${(t.sampleRate / 1000).toFixed(1)} kHz`);
  if (t.bitrate) parts.push(`${t.bitrate} kbps`);
  return parts.join(' · ');
}

// ---- library cleanup (v9): pruning reports, trash, delete list, fake lossless, preview ----

export type LosslessVerdict = 'ok' | 'lossy_source' | 'suspect' | 'upsampled' | 'unknown' | 'error';
export type CleanupReportId = 'never_played' | 'unplayed_albums' | 'disliked' | 'low_quality' | 'fake_lossless' | 'short';

export interface CleanupReport {
  id: CleanupReportId;
  title: string;
  description: string;
  kind: 'track' | 'album';
  threshold: number | null;
  unit: 'days' | 'skips' | 'kbps' | 'seconds' | null;
  count: number;
  bytes: number;
}

export interface CleanupItem {
  kind: 'track' | 'album';
  id: string;
  title: string;
  artist: string;
  album: string;
  albumId: string;
  path: string | null;
  bytes: number | null;
  durationS: number | null;
  codec: string | null;
  bitrate: number | null;
  sampleRate: number | null;
  bitsPerSample: number | null;
  plays: number;
  skips: number;
  blockedBy: number;
  lastPlayed: string | null;
  addedAt: string | null;
  note: string | null;
  verdict?: LosslessVerdict;
  trackCount: number;
  trackIds: string[];
}

export interface CleanupSummary {
  reports: CleanupReport[];
  inTrash: number;
  deleteList: number;
  lossless: { lossless: number; checked: number; flagged: number; byVerdict: Record<string, number> };
  trashDays: number;
  deleteFiles: boolean;
}

export interface TrashBatch {
  batchId: string;
  removedAt: string;
  removedBy: string | null;
  reason: string | null;
  purgeAfter: string;
  count: number;
  bytes: number;
  purgedCount: number;
  purgeError: string | null;
  sample: { id: string; title: string; artist: string; album: string; path: string }[];
}

export interface CleanupSearch {
  artists: { id: string; name: string; trackCount: number; bytes: number }[];
  albums: { id: string; title: string; artist: string; year: number | null; trackCount: number; bytes: number; path: string | null }[];
  tracks: CleanupItem[];
}

export interface PurgeResult {
  purged: number;
  filesDeleted: number;
  freedBytes: number;
  kept: number;
  errors: number;
}

export function cleanupApi(c: ApiClient) {
  const enc = encodeURIComponent;
  return {
    summary: () => c.get<CleanupSummary>('/admin/cleanup/summary'),
    report: (id: CleanupReportId, q: { threshold?: number; limit?: number; offset?: number } = {}) =>
      c.get<{ total: number; bytes: number; items: CleanupItem[] }>(`/admin/cleanup/reports/${enc(id)}`, q),
    search: (q: string) => c.get<CleanupSearch>('/admin/cleanup/search', { q }),
    remove: (b: { trackIds?: string[]; albumIds?: string[]; artistIds?: string[]; reason?: string | null }) =>
      c.post<{ removed: number; batchId: string; purgeAfter: string; bytes: number }>('/admin/cleanup/remove', b),
    trash: () => c.get<{ trashDays: number; deleteFiles: boolean; batches: TrashBatch[] }>('/admin/cleanup/trash'),
    restore: (b: { batchIds?: string[]; trackIds?: string[] }) => c.post<{ restored: number }>('/admin/cleanup/trash/restore', b),
    purge: (batchIds: string[]) => c.post<PurgeResult>('/admin/cleanup/trash/purge', { batchIds }),
    deleteList: () => c.get<{ id: string; path: string; bytes: number | null; error: string | null }[]>('/admin/cleanup/delete-list'),
    deleteScriptUrl: () => c.downloadUrl('/admin/cleanup/delete-list?format=sh'),
    dismissLossless: (trackIds: string[], dismissed = true) => c.post<{ updated: number }>('/admin/cleanup/lossless/dismiss', { trackIds, dismissed }),
    /** <audio> URL for any copy (hidden/removed too): the file as stored, or Opus. '' in mock mode. */
    previewUrl: (trackId: string, format: 'original' | 'opus' = 'original') =>
      c.mediaUrl(`/admin/preview/${enc(trackId)}?format=${format}`),
  };
}

/** Plain-English label for a fake-lossless verdict, or null when there is nothing to say. */
export function losslessLabel(v: LosslessVerdict | null | undefined): string | null {
  switch (v) {
    case 'lossy_source':
      return 'Fake lossless';
    case 'suspect':
      return 'Possibly fake lossless';
    case 'upsampled':
      return 'Upsampled';
    case 'ok':
      return 'Real lossless';
    default:
      return null;
  }
}

/** "120 songs · 1.2 GB" for a selection. */
export function selectionText(items: Pick<CleanupItem, 'trackIds' | 'bytes'>[], fmt: (n: number) => string): string {
  const songs = items.reduce((n, i) => n + i.trackIds.length, 0);
  const bytes = items.reduce((n, i) => n + (i.bytes ?? 0), 0);
  return `${songs} song${songs === 1 ? '' : 's'} · ${fmt(bytes)}`;
}
