// Mock of the admin console endpoints (see admin.ts). State lives here; mock.ts routes /admin/* to it.
import type {
  AdminSettings,
  AutotagField,
  AutotagGroup,
  AutotagStatus,
  AutotagSuggestion,
  DuplicateGroup,
  Backup,
  OffsiteStatus,
  BugReportDetail,
  FailedFile,
  HealthCheck,
  HealthItem,
  JobRun,
  JobStatus,
  LogEntry,
  NowPlayingRow,
  ServerStatus,
} from './admin';
import type { Track } from '../types';

const iso = (ms = Date.now()) => new Date(ms).toISOString();
const GB = 1024 ** 3;

export class AdminError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

const LOGGERS = ['nasmusic.app', 'nasmusic.library.scan', 'nasmusic.stream', 'nasmusic.ai.analyse', 'nasmusic.connect', 'uvicorn.access'];
const MESSAGES: [string, string][] = [
  ['INFO', 'GET /api/v1/home 200 (14 ms)'],
  ['INFO', 'scan: 0 new, 0 changed, 0 missing (2.1 s)'],
  ['DEBUG', 'opus cache hit for t3_2 (160 kbps)'],
  ['INFO', 'connect: device d-web joined (user admin)'],
  ['WARNING', 'analyse: "Fireflies Underground.flac" took 41 s (slow)'],
  ['INFO', 'GET /api/v1/stream/t0_1?format=opus 206 (3 ms)'],
  ['ERROR', 'analyse: failed to decode "Broken Track.flac": invalid frame header'],
  ['INFO', 'lyrics: LRCLIB hit for t5_3 (synced)'],
];

export class MockAdmin {
  startedAt = Date.now() - 3 * 86400e3 - 4 * 3600e3;
  logs: LogEntry[] = [];
  private seq = 0;
  jobs: JobStatus[] = [
    { name: 'scan', state: 'idle', startedAt: iso(Date.now() - 300e3), finishedAt: iso(Date.now() - 297e3), ok: true, durationS: 2.9, nextRunAt: iso(Date.now() + 1e3 * 60 * 2) },
    { name: 'analyse', state: 'running', startedAt: iso(Date.now() - 1800e3), ok: null },
    { name: 'nightly', state: 'idle', startedAt: iso(Date.now() - 10 * 3600e3), finishedAt: iso(Date.now() - 10 * 3600e3 + 95e3), ok: true, durationS: 95, result: { mixes: 7 }, nextRunAt: iso(Date.now() + 14 * 3600e3) },
    { name: 'discovery', state: 'failed', startedAt: iso(Date.now() - 3 * 86400e3), finishedAt: iso(Date.now() - 3 * 86400e3 + 12e3), ok: false, durationS: 12, error: 'MusicBrainz: 503 Service Unavailable', nextRunAt: iso(Date.now() + 4 * 86400e3) },
    { name: 'pretranscode', state: 'idle', startedAt: iso(Date.now() - 9 * 3600e3), finishedAt: iso(Date.now() - 9 * 3600e3 + 400e3), ok: true, durationS: 400 },
  ];
  history: JobRun[] = [];
  failed: FailedFile[] = [
    { id: 'f1', path: 'Broken Artist/2019 - Glitch/03 Broken Track.flac', stage: 'analyse', error: 'invalid frame header', attempts: 3, lastTriedAt: iso(Date.now() - 7200e3) },
    { id: 'f2', path: 'Various/Live at Nowhere/11 Encore.mp3', stage: 'scan', error: 'mutagen: unable to read ID3 tag', attempts: 1, lastTriedAt: iso(Date.now() - 86400e3) },
  ];
  analysis = { done: 812, total: 1340, failed: 2 };
  checks: (HealthCheck & { items: HealthItem[] })[] = [
    {
      id: 'missing',
      title: 'Missing files',
      description: 'Tracks in the database whose file is gone from the library.',
      count: 3,
      fix: { label: 'Remove from database', destructive: true },
      items: [
        { id: 'm1', title: 'Old Song', path: 'Old Band/1999/01 Old Song.flac' },
        { id: 'm2', title: 'Demo Take', path: 'Demos/Demo Take.flac' },
        { id: 'm3', title: 'Interlude', path: 'Various/Interlude.mp3' },
      ],
    },
    {
      id: 'duplicates',
      title: 'Duplicates',
      description: 'The same recording more than once (same artist, title and duration).',
      count: 2,
      fix: null,
      items: [
        { id: 'd1', title: 'Afterglow — Velvet Static', detail: '2 copies', group: 'Velvet Static/2008 - Afterglow · Singles/Afterglow' },
        { id: 'd2', title: 'Low Tide — The Marrow Lanes', detail: '2 copies', group: 'The Marrow Lanes/2015 - Low Tide · Compilations/Low Tide' },
      ],
    },
    { id: 'missing_tags', title: 'Missing tags', description: 'No artist, album or title tag.', count: 5, fix: null, items: Array.from({ length: 5 }, (_, i) => ({ id: 't' + i, title: `track0${i + 1}.flac`, detail: 'no artist / album', path: `Unsorted/track0${i + 1}.flac` })) },
    { id: 'missing_art', title: 'Missing cover art', description: 'Albums without embedded art or cover.jpg.', count: 4, fix: null, items: Array.from({ length: 4 }, (_, i) => ({ id: 'a' + i, title: `Album ${i + 1}`, path: `Some Artist/Album ${i + 1}` })) },
    { id: 'unanalysed', title: 'Never analysed', description: 'Not analysed yet (queued) or skipped.', count: 528, fix: { label: 'Queue for analysis', destructive: false }, items: [] },
    { id: 'orphan_cache', title: 'Orphaned caches', description: 'Opus and art cache files for tracks that no longer exist.', count: 41, bytes: 0.62 * GB, fix: { label: 'Clean', destructive: true }, items: [] },
    { id: 'old_backups', title: 'Old backups', description: 'Backups beyond the newest 5.', count: 2, bytes: 0.31 * GB, fix: { label: 'Delete old backups', destructive: true }, items: [] },
  ];
  reports: BugReportDetail[] = [
    {
      id: 'r1',
      createdAt: iso(Date.now() - 5 * 3600e3),
      username: 'admin',
      deviceName: 'Pixel 8',
      kind: 'android',
      appVersion: '0.1.42',
      title: 'For You crashes when tapping a mix',
      status: 'open',
      description: 'Opened For You, tapped Daily Mix 1, app closed.',
      logs: 'E/AndroidRuntime: FATAL EXCEPTION: main\njava.lang.IllegalStateException: MediaController method called from a wrong thread',
      context: { android: 14, model: 'Pixel 8' },
    },
    { id: 'r2', createdAt: iso(Date.now() - 2 * 86400e3), username: 'sam', deviceName: 'Firefox on Linux', kind: 'web', appVersion: '0.1.0', title: 'Lyrics stay on previous song', status: 'closed', description: 'After skipping, lyrics showed the old song until I reopened them.', logs: null, context: null },
  ];
  backups: Backup[] = [
    { id: 'b3', createdAt: iso(Date.now() - 86400e3), bytes: 48e6, kind: 'scheduled' },
    { id: 'b2', createdAt: iso(Date.now() - 8 * 86400e3), bytes: 46e6, kind: 'scheduled' },
    { id: 'b1', createdAt: iso(Date.now() - 20 * 86400e3), bytes: 31e6, kind: 'manual', note: 'before v2 migration' },
  ];
  offsite: OffsiteStatus = {
    configured: true,
    destination: '/mnt/backup/nasmusic',
    kind: 'folder',
    keep: 14,
    includeConfig: true,
    last: { at: iso(Date.now() - 10 * 3600e3), ok: true, error: null, name: 'nasmusic-scheduled-20261124T041500Z.db.gz' },
    items: [
      { name: 'nasmusic-scheduled-20261124T041500Z.db.gz', kind: 'scheduled', bytes: 16e6, createdAt: iso(Date.now() - 10 * 3600e3) },
      { name: 'nasmusic-scheduled-20261123T041500Z.db.gz', kind: 'scheduled', bytes: 16e6, createdAt: iso(Date.now() - 34 * 3600e3) },
    ],
    error: null,
  };
  settings: AdminSettings = { lanUrl: 'https://192.168.68.2:8443', tunnelUrl: 'https://music.example.com', timezone: 'Europe/London', opusCacheMaxGb: 50, lrclib: true };

  constructor() {
    for (let i = 0; i < 60; i++) this.addLog(Date.now() - (60 - i) * 4000);
    setInterval(() => {
      if (Math.random() < 0.7) this.addLog();
      if (this.analysis.done < this.analysis.total) this.analysis.done += 1;
    }, 1500);
    const names = ['scan', 'scan', 'nightly', 'analyse', 'discovery', 'pretranscode', 'scan', 'backup'];
    this.history = names.map((name, i) => ({
      id: 'run' + i,
      name,
      startedAt: iso(Date.now() - (i + 1) * 3 * 3600e3),
      finishedAt: iso(Date.now() - (i + 1) * 3 * 3600e3 + 30e3),
      ok: name !== 'discovery',
      durationS: 30,
      error: name === 'discovery' ? 'MusicBrainz: 503 Service Unavailable' : null,
      trigger: name === 'scan' ? 'schedule' : 'manual',
      summary: name === 'scan' ? '0 new, 0 changed' : name === 'nightly' ? '7 mixes for 2 users' : null,
    }));
  }

  addLog(at = Date.now(), level?: string, message?: string, logger?: string) {
    const [lv, msg] = level && message ? [level, message] : MESSAGES[Math.floor(Math.random() * MESSAGES.length)];
    this.logs.push({ seq: ++this.seq, at: iso(at), level: lv, logger: logger ?? LOGGERS[Math.floor(Math.random() * LOGGERS.length)], message: msg });
    if (this.logs.length > 2000) this.logs.shift();
  }

  status(lib: { tracks: number; albums: number; artists: number }): ServerStatus {
    const t = Date.now() / 1000;
    const remaining = this.analysis.total - this.analysis.done;
    return {
      version: '3.0.0-mock',
      uptimeS: Math.floor((Date.now() - this.startedAt) / 1000),
      cpuPct: Math.round(18 + 12 * Math.sin(t / 7) + Math.random() * 6),
      load: [0.82, 0.71, 0.66],
      memUsedBytes: 6.3 * GB + Math.sin(t / 11) * 0.2 * GB,
      memTotalBytes: 16 * GB,
      disks: [
        { name: 'Music library (read-only)', path: '/music', usedBytes: 1.42e12, totalBytes: 3.6e12 },
        { name: 'Data volume', path: '/data', usedBytes: 61 * GB, totalBytes: 220 * GB },
      ],
      dataBytes: 61 * GB,
      library: { ...lib, missing: 3 },
      analysis: { ...this.analysis, ratePerHour: 120, etaS: remaining > 0 ? (remaining / 120) * 3600 : 0, modelVersion: 'discogs-effnet-bs64-1' },
      jobs: this.jobs,
    };
  }

  nowPlaying(rows: { userId: string; username: string; deviceId: string; deviceName: string; kind: NowPlayingRow['kind']; playing: boolean; positionMs: number; track: Track | null }[]): NowPlayingRow[] {
    return rows;
  }

  runJob(name: string): JobStatus {
    const j = this.jobs.find((x) => x.name === name) ?? { name, state: 'idle' as const };
    if (!this.jobs.includes(j)) this.jobs.push(j);
    if (j.state === 'running') throw new AdminError(409, `${name} is already running`);
    j.state = 'running';
    j.startedAt = iso();
    j.finishedAt = null;
    j.ok = null;
    j.error = null;
    this.addLog(Date.now(), 'INFO', `${name}: started (manual)`, 'nasmusic.jobs');
    setTimeout(() => {
      j.state = 'idle';
      j.finishedAt = iso();
      j.ok = true;
      j.durationS = 4;
      this.addLog(Date.now(), 'INFO', `${name}: finished in 4.0 s`, 'nasmusic.jobs');
      this.history.unshift({ id: 'run' + Date.now(), name, startedAt: j.startedAt!, finishedAt: j.finishedAt, ok: true, durationS: 4, trigger: 'manual', summary: null });
      if (name === 'backup') this.backups.unshift({ id: 'b' + Date.now(), createdAt: iso(), bytes: 49e6, kind: 'manual' });
    }, 4000);
    return j;
  }

  retry(id?: string) {
    const n = id ? this.failed.filter((f) => f.id === id).length : this.failed.length;
    if (id && !n) throw new AdminError(404, 'not found');
    this.failed = id ? this.failed.filter((f) => f.id !== id) : [];
    this.addLog(Date.now(), 'INFO', `retry: ${n} file(s) queued`, 'nasmusic.library.scan');
    return { queued: n };
  }

  health(): HealthCheck[] {
    return this.checks.map(({ items: _i, ...c }) => ({ ...c, bytes: c.bytes ?? null }));
  }

  healthItems(id: string): HealthItem[] {
    const c = this.checks.find((x) => x.id === id);
    if (!c) throw new AdminError(404, 'unknown check');
    return c.items;
  }

  fix(id: string, ids?: string[]) {
    const c = this.checks.find((x) => x.id === id);
    if (!c) throw new AdminError(404, 'unknown check');
    if (!c.fix) throw new AdminError(400, 'this check cannot be fixed automatically');
    const n = ids?.length ? ids.length : c.count;
    const freed = c.bytes && c.count ? (c.bytes * n) / c.count : null;
    c.items = ids?.length ? c.items.filter((i) => !ids.includes(i.id)) : [];
    c.count = Math.max(0, c.count - n);
    if (c.bytes) c.bytes = Math.max(0, c.bytes - (freed ?? 0));
    if (id === 'old_backups') this.backups = this.backups.slice(0, 5);
    this.addLog(Date.now(), 'INFO', `health: ${c.fix.label.toLowerCase()} — ${n} item(s)`, 'nasmusic.admin');
    return { fixed: n, freedBytes: freed };
  }

  logsAfter(after: number, level: string | null, q: string | null, limit: number): LogEntry[] {
    const order = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
    const min = level ? order.indexOf(level) : 0;
    const needle = (q ?? '').toLowerCase();
    return this.logs.filter((e) => e.seq > after && order.indexOf(e.level) >= min && (!needle || e.message.toLowerCase().includes(needle))).slice(-limit);
  }

  saveSettings(b: Partial<AdminSettings>) {
    const restart: string[] = [];
    for (const k of Object.keys(b) as (keyof AdminSettings)[]) {
      if (JSON.stringify(b[k]) !== JSON.stringify(this.settings[k]) && (k === 'timezone' || k === 'lanUrl')) restart.push(k);
    }
    this.settings = { ...this.settings, ...b };
    return { values: this.settings, restartRequired: restart };
  }

  // ---- tag fixing + duplicates (v6) ----
  autotag: AutotagGroup[] = [
    {
      groupId: 'al1~rel1',
      albumId: 'al1',
      currentAlbum: 'spotify download',
      currentAlbumArtist: 'Various Artists',
      albumHasArt: false,
      confidence: 0.93,
      albumTrackCount: 2,
      release: {
        mbid: '00000000-0000-0000-0000-00000000aaaa',
        title: 'Aurora Lane',
        artist: 'The Lanterns',
        year: 2019,
        country: 'GB',
        format: 'Digital Media',
        trackCount: 10,
        type: 'Album',
        hasCover: false,
      },
      tracks: [1, 2].map((n) => ({
        trackId: `t0_${n}`,
        path: `spotify download/Song ${n} (2).flac`,
        status: 'pending' as const,
        confidence: n === 1 ? 0.96 : 0.88,
        current: {
          title: `Song ${n} (2)`,
          artist: 'The Lanterns',
          albumArtist: null,
          album: 'spotify download',
          trackNo: null,
          discNo: null,
          year: null,
          genre: null,
        },
        suggested: {
          title: `Song ${n}`,
          artist: 'The Lanterns',
          albumArtist: 'The Lanterns',
          album: 'Aurora Lane',
          trackNo: n,
          discNo: 1,
          year: 2019,
          genre: 'Indie Pop',
          mbRecordingId: `00000000-0000-0000-0000-00000000000${n}`,
        },
        changes: ['title', 'albumArtist', 'album', 'trackNo', 'discNo', 'year', 'genre', 'mbRecordingId'] as AutotagField[],
        defaultFields: ['title', 'albumArtist', 'album', 'trackNo', 'discNo', 'year', 'genre', 'mbRecordingId'] as AutotagField[],
        detail: { acoustidScore: 0.97, durationDiffS: 0.4, onRelease: true },
      })),
    },
  ];
  dups: DuplicateGroup[] = [
    {
      groupId: 't0_1',
      similarity: 0.97,
      hiddenCount: 0,
      tracks: [
        {
          id: 't0_1',
          path: 'The Lanterns/Aurora Lane/01 Song 1.flac',
          title: 'Song 1',
          artist: 'The Lanterns',
          album: 'Aurora Lane',
          durationS: 201,
          codec: 'FLAC',
          bitrate: 900,
          sampleRate: 44100,
          bitsPerSample: 16,
          bytes: 23e6,
          hidden: false,
          keptId: null,
          recommended: true,
        },
        {
          id: 't0_2',
          path: 'spotify download/Song 1 (2).mp3',
          title: 'Song 1',
          artist: 'The Lanterns',
          album: 'spotify download',
          durationS: 201,
          codec: 'MP3',
          bitrate: 320,
          sampleRate: 44100,
          bitsPerSample: null,
          bytes: 8e6,
          hidden: false,
          keptId: null,
          recommended: false,
        },
      ],
    },
  ];
  autotagStatus(): AutotagStatus {
    const all = this.autotag.flatMap((g) => g.tracks);
    const n = (s: string) => all.filter((t) => t.status === s).length;
    return {
      apiKeyConfigured: true,
      fpcalcAvailable: true,
      running: false,
      tracks: 1340,
      fingerprinted: 1210,
      fingerprintFailed: 3,
      matched: 1100,
      noMatch: 107,
      lookupErrors: 0,
      pending: n('pending'),
      accepted: n('accepted'),
      rejected: n('rejected'),
    };
  }
  autotagGroups(status: string) {
    const groups = this.autotag
      .map((g) => ({
        ...g,
        tracks: g.tracks.filter((t) => t.status === status),
      }))
      .filter((g) => g.tracks.length);
    return { total: groups.length, groups };
  }
  autotagDecide(
    b: { trackIds?: string[]; groupIds?: string[]; minConfidence?: number },
    to: AutotagSuggestion['status'],
    from: AutotagSuggestion['status'] = 'pending',
  ) {
    let n = 0;
    for (const g of this.autotag)
      for (const t of g.tracks)
        if (
          t.status === from &&
          (b.trackIds?.includes(t.trackId) || b.groupIds?.includes(g.groupId) || (b.minConfidence != null && t.confidence >= b.minConfidence))
        ) {
          t.status = to;
          n++;
        }
    return n;
  }
  resolveDup(keep: string, hide: string[]) {
    for (const g of this.dups)
      for (const t of g.tracks)
        if (hide.includes(t.id)) {
          t.hidden = true;
          t.keptId = keep;
        } else if (t.id === keep) t.hidden = false;
    for (const g of this.dups) g.hiddenCount = g.tracks.filter((t) => t.hidden).length;
    return { hidden: hide.length, keptId: keep };
  }
  unhideDup(ids: string[]) {
    let n = 0;
    for (const g of this.dups) {
      for (const t of g.tracks)
        if (ids.includes(t.id) && t.hidden) {
          t.hidden = false;
          t.keptId = null;
          n++;
        }
      g.hiddenCount = g.tracks.filter((t) => t.hidden).length;
    }
    return { unhidden: n };
  }
}
