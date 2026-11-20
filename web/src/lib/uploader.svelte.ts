// Upload queue for the web/desktop player. Lives outside the page, so uploads carry on while you browse.
// Two files at a time; each is resumable (see sendFile) and can be retried or cancelled.
import { ApiError } from './api/client';
import { allowedName, sendFile, uploadsApi, type UploadOverview, type UploadVisibility } from './api/uploads';
import { session } from './session.svelte';
import { inspect, previewPath, type Sniffed, type TagPreview } from './tagpreview';

export type ItemState = 'checking' | 'queued' | 'uploading' | 'done' | 'failed' | 'rejected' | 'cancelled';

export interface UploadItem {
  key: number;
  file: File;
  state: ItemState;
  sent: number;
  error: string | null;
  format: Sniffed | null;
  tags: TagPreview;
  target: string;
  sessionId: string | null;
  visibility: UploadVisibility;
  trackId: string | null;
}

const PARALLEL = 2;

class Uploader {
  items = $state<UploadItem[]>([]);
  overview = $state<UploadOverview | null>(null);
  visibility = $state<UploadVisibility>('everyone');
  private aborts = new Map<number, AbortController>();
  private seq = 0;
  /** Bumped when a file finished, so lists reload. */
  rev = $state(0);

  get api() {
    return uploadsApi(session.api);
  }

  get active(): number {
    return this.items.filter((i) => i.state === 'uploading' || i.state === 'queued' || i.state === 'checking').length;
  }

  async load() {
    try {
      this.overview = await this.api.overview();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) throw new Error('This server doesn’t support uploads yet (needs a server update).', { cause: e });
      throw e;
    }
  }

  private find(key: number) {
    return this.items.find((i) => i.key === key);
  }

  async add(files: Iterable<File>) {
    for (const file of files) {
      const key = ++this.seq;
      this.items.push({
        key, file, state: 'checking', sent: 0, error: null, format: null, tags: {}, target: '', sessionId: null,
        visibility: this.visibility, trackId: null,
      });
      void this.check(key);
    }
  }

  private async check(key: number) {
    const it = this.find(key);
    if (!it) return;
    const max = this.overview?.maxFileBytes;
    if (!allowedName(it.file.name)) return this.reject(key, 'Not a supported file type (FLAC, MP3, M4A/AAC, Ogg/Opus, WAV)');
    if (max && it.file.size > max) return this.reject(key, 'Larger than the server’s file size limit');
    const { format, tags } = await inspect(it.file);
    const cur = this.find(key);
    if (!cur) return;
    cur.format = format;
    cur.tags = tags;
    cur.target = previewPath(tags, cur.file.name);
    if (!format) return this.reject(key, 'This doesn’t look like an audio file');
    cur.state = 'queued';
    this.pump();
  }

  private reject(key: number, msg: string) {
    const it = this.find(key);
    if (it) {
      it.state = 'rejected';
      it.error = msg;
    }
  }

  private pump() {
    while (this.items.filter((i) => i.state === 'uploading').length < PARALLEL) {
      const next = this.items.find((i) => i.state === 'queued');
      if (!next) return;
      void this.run(next.key);
    }
  }

  private async run(key: number) {
    const it = this.find(key);
    if (!it) return;
    it.state = 'uploading';
    it.error = null;
    const ac = new AbortController();
    this.aborts.set(key, ac);
    try {
      const s = await sendFile(this.api, it.file, {
        visibility: it.visibility,
        chunkBytes: this.overview?.chunkBytes ?? 4 * 1024 * 1024,
        sessionId: it.sessionId ?? undefined,
        signal: ac.signal,
        onSession: (s) => {
          const c = this.find(key);
          if (c) c.sessionId = s.id;
        },
        onProgress: (sent) => {
          const c = this.find(key);
          if (c) c.sent = sent;
        },
      });
      const c = this.find(key);
      if (c) {
        c.state = 'done';
        c.trackId = s.upload?.trackId ?? null;
        if (s.upload) c.target = s.upload.path;
      }
      this.rev++;
      void this.load().catch(() => {});
    } catch (e) {
      const c = this.find(key);
      if (c && c.state !== 'cancelled') {
        c.state = 'failed';
        c.error = (e as Error).message || 'Upload failed';
        // A refused or failed session cannot be resumed: start a new one on retry.
        if (e instanceof ApiError) c.sessionId = null;
      }
    } finally {
      this.aborts.delete(key);
      this.pump();
    }
  }

  retry(key: number) {
    const it = this.find(key);
    if (it && (it.state === 'failed' || it.state === 'cancelled')) {
      it.state = 'queued';
      it.error = null;
      this.pump();
    }
  }

  async cancel(key: number) {
    const it = this.find(key);
    if (!it) return;
    const was = it.state;
    it.state = 'cancelled';
    this.aborts.get(key)?.abort();
    if (it.sessionId && was !== 'done') {
      await this.api.cancel(it.sessionId).catch(() => {});
      it.sessionId = null;
      it.sent = 0;
    }
    this.pump();
  }

  clearFinished() {
    this.items = this.items.filter((i) => !['done', 'rejected', 'cancelled'].includes(i.state));
  }
}

export const uploader = new Uploader();
