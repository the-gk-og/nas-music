// Uploads (api/openapi.yaml 0.7.0, schema v7): resumable chunk + offset uploads into the server's upload
// folder, the caller's uploaded files, and the admin's per-user permission / quota.
import { ApiError, type ApiClient } from './client';

export type UploadVisibility = 'everyone' | 'private';
export type UploadFormat = 'flac' | 'mp3' | 'm4a' | 'aac' | 'ogg' | 'opus' | 'wav';

export interface UploadedFile {
  id: string;
  userId: string | null;
  username: string | null;
  path: string;
  trackId: string;
  bytes: number;
  originalName: string;
  format: UploadFormat;
  visibility: UploadVisibility;
  createdAt: string;
  indexed: boolean;
  title: string | null;
  artist: string | null;
  album: string | null;
  durationS: number | null;
}

export interface UploadSession {
  id: string;
  filename: string;
  bytes: number;
  offset: number;
  visibility: UploadVisibility;
  status: 'uploading' | 'done' | 'failed';
  error: string | null;
  createdAt?: string;
  updatedAt?: string;
  upload: UploadedFile | null;
}

export interface UploadOverview {
  canUpload: boolean;
  enabled: boolean;
  quotaBytes: number | null;
  usedBytes: number;
  maxFileBytes: number;
  chunkBytes: number;
  maxChunkBytes: number;
  formats: UploadFormat[];
  files: UploadedFile[];
  sessions: UploadSession[];
}

export interface AdminUploadUser {
  userId: string;
  username: string;
  isAdmin: boolean;
  canUpload: boolean;
  explicit: boolean;
  quotaBytes: number | null;
  usedBytes: number;
  files: number;
}

export interface AdminUploads {
  enabled: boolean;
  defaultQuotaBytes: number | null;
  maxFileBytes: number;
  users: AdminUploadUser[];
}

/** 409 from PATCH: the server has a different offset; resume from `offset`. */
export class OffsetMismatch extends Error {
  constructor(public offset: number) {
    super(`server has ${offset} bytes`);
  }
}

export const ACCEPT = '.flac,.mp3,.m4a,.mp4,.aac,.ogg,.oga,.opus,.wav,audio/*';
const EXTS = ['.flac', '.mp3', '.m4a', '.mp4', '.aac', '.ogg', '.oga', '.opus', '.wav'];

export function allowedName(name: string): boolean {
  const n = name.toLowerCase();
  return EXTS.some((e) => n.endsWith(e));
}

export function uploadsApi(c: ApiClient) {
  const enc = encodeURIComponent;
  return {
    overview: (all = false) => c.get<UploadOverview>('/uploads', all ? { all: true } : undefined),
    create: (b: { filename: string; bytes: number; visibility: UploadVisibility }) => c.post<UploadSession>('/uploads', b),
    status: (id: string) => c.get<UploadSession>(`/uploads/${enc(id)}`),
    cancel: (id: string) => c.del<void>(`/uploads/${enc(id)}`),
    /** Append one chunk at `offset`. Throws OffsetMismatch on 409 (with the server's offset). */
    async append(id: string, offset: number, chunk: Blob, signal?: AbortSignal): Promise<UploadSession> {
      const headers: Record<string, string> = {
        Accept: 'application/json',
        'Content-Type': 'application/offset+octet-stream',
        'Upload-Offset': String(offset),
      };
      if (c.mode === 'bearer' && c.token) headers.Authorization = `Bearer ${c.token}`;
      const res = await c.fetchRaw(`${c.base}/uploads/${enc(id)}`, {
        method: 'PATCH',
        headers,
        body: chunk,
        signal,
        credentials: c.mode === 'cookie' ? 'include' : 'omit',
      });
      let j: { detail?: unknown; offset?: number } & Partial<UploadSession> = {};
      try {
        j = await res.json();
      } catch {
        /* not JSON */
      }
      if (res.status === 409 && typeof j.offset === 'number') throw new OffsetMismatch(j.offset);
      if (!res.ok) throw new ApiError(res.status, typeof j.detail === 'string' ? j.detail : res.statusText);
      return j as UploadSession;
    },
    setVisibility: (uploadId: string, visibility: UploadVisibility) =>
      c.patch<UploadedFile>(`/uploads/files/${enc(uploadId)}`, { visibility }),
    remove: (uploadId: string) => c.del<{ deleted: string; trackId: string }>(`/uploads/files/${enc(uploadId)}`),
    admin: () => c.get<AdminUploads>('/admin/uploads'),
    setUser: (userId: string, b: { canUpload?: boolean | null; quotaBytes?: number | null }) =>
      c.put<AdminUploadUser>(`/admin/users/${enc(userId)}/uploads`, b),
  };
}

export type UploadsApi = ReturnType<typeof uploadsApi>;

/**
 * Send a file with resume: creates a session (unless `sessionId`), then PATCHes chunks. On a 409 it jumps to the
 * server's offset; on a network error it asks the server where it is and carries on, up to `retries` times in a
 * row (with a growing pause). Reports progress after every chunk.
 */
export async function sendFile(
  api: Pick<UploadsApi, 'create' | 'status' | 'append'>,
  file: Blob & { name: string },
  opts: {
    visibility: UploadVisibility;
    chunkBytes: number;
    sessionId?: string;
    retries?: number;
    signal?: AbortSignal;
    sleep?: (ms: number) => Promise<void>;
    onSession?: (s: UploadSession) => void;
    onProgress?: (sent: number, total: number) => void;
  },
): Promise<UploadSession> {
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const retries = opts.retries ?? 5;
  let s: UploadSession = opts.sessionId
    ? await api.status(opts.sessionId)
    : await api.create({ filename: file.name, bytes: file.size, visibility: opts.visibility });
  opts.onSession?.(s);
  let offset = s.offset;
  let failures = 0;
  opts.onProgress?.(offset, file.size);
  while (s.status === 'uploading') {
    if (opts.signal?.aborted) throw new DOMException('aborted', 'AbortError');
    const end = Math.min(file.size, offset + opts.chunkBytes);
    try {
      s = await api.append(s.id, offset, file.slice(offset, end), opts.signal);
      offset = s.offset;
      failures = 0;
    } catch (e) {
      if (e instanceof OffsetMismatch) {
        offset = e.offset;
        continue;
      }
      if (e instanceof ApiError || (e as Error)?.name === 'AbortError') throw e; // the server said no
      if (++failures > retries) throw e;
      await sleep(Math.min(30_000, 1000 * 2 ** (failures - 1)));
      try {
        s = await api.status(s.id);
        offset = s.offset;
      } catch {
        /* still offline: try the chunk again */
      }
    }
    opts.onProgress?.(offset, file.size);
  }
  if (s.status === 'failed') throw new ApiError(415, s.error ?? 'upload failed');
  return s;
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}
