// Admin console API (/api/v1/admin/*). Shapes are this client's proposal until api/openapi.yaml defines
// them; everything admin goes through this one module so adopting the real contract touches one file.
import type { ApiClient } from './client';
import type { DeviceKind, MyDevice, Track } from '../types';

export interface DiskUsage {
  name: string;
  path: string;
  usedBytes: number;
  totalBytes: number;
}

export type JobName = 'scan' | 'analyse' | 'loudness' | 'nightly' | 'discovery' | 'pretranscode' | 'backup';

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

    settings: () => c.get<AdminSettings>('/admin/settings'),
    saveSettings: (s: Partial<AdminSettings>) => c.put<SettingsSaveResult>('/admin/settings', s),
  };
}

export type AdminApi = ReturnType<typeof adminApi>;

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
