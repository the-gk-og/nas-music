// Podcasts, audiobooks and internet radio (openapi 0.7.0, tag "spoken"). Not music: nothing here posts
// /events, scrobbles, or touches queues/playlists, so the recommender and Wrapped never see it.
import type { ApiClient } from './api/client';

export interface SpokenProgress {
  kind: 'episode' | 'book';
  id: string;
  positionS: number;
  durationS: number | null;
  played: boolean;
  speed: number | null;
  updatedAt: string;
  deviceId?: string | null;
}

export interface Podcast {
  id: string;
  feedUrl: string;
  title: string;
  author: string | null;
  description: string | null;
  summary: string | null;
  link: string | null;
  explicit: boolean;
  hasImage: boolean;
  lastPolledAt: string | null;
  lastError: string | null;
  serverKeepLast: number | null;
  subscribed: boolean;
  speed: number;
  skipSilence: boolean;
  autoDownload: boolean;
  unplayed?: number;
  latestAt?: string | null;
}

export interface PodcastSearchResult {
  id: string;
  title: string;
  author: string | null;
  feedUrl: string;
  imageUrl: string | null;
  explicit: boolean;
  genre: string | null;
  source: 'podcastindex' | 'itunes';
  subscribed: boolean;
}

export interface Chapter {
  start: number;
  title: string;
  url?: string;
}

export interface Episode {
  id: string;
  podcastId: string;
  podcastTitle?: string;
  title: string;
  summary: string | null;
  publishedAt: string | null;
  durationS: number | null;
  explicit: boolean;
  onServer: boolean;
  hasChapters: boolean;
  progress: SpokenProgress | null;
}

export interface EpisodeDetail extends Episode {
  description: string | null;
  chapters: Chapter[];
  speed: number;
  skipSilence: boolean;
}

export interface Audiobook {
  id: string;
  title: string;
  author: string | null;
  narrator: string | null;
  year: number | null;
  durationS: number;
  summary: string | null;
  progress: SpokenProgress | null;
}

export interface BookPart {
  index: number;
  durationS: number;
  startS: number;
  contentType: string;
  name: string;
}

export interface Bookmark {
  id: string;
  positionS: number;
  note: string | null;
  createdAt: string;
}

export interface AudiobookDetail extends Audiobook {
  description: string | null;
  parts: BookPart[];
  chapters: { index: number; title: string; startS: number }[];
  bookmarks: Bookmark[];
}

export interface Station {
  id: string;
  name: string;
  streamUrl: string;
  homepage: string | null;
  country: string | null;
  tags: string[];
  codec: string | null;
  bitrate: number | null;
  explicit: boolean;
  hasImage: boolean;
  favourite: boolean;
}

export interface StationSearchResult {
  id: string;
  name: string;
  streamUrl: string;
  homepage: string | null;
  imageUrl: string | null;
  country: string | null;
  tags: string[];
  codec: string | null;
  bitrate: number | null;
  explicit: boolean;
  radiobrowserUuid: string | null;
  favourite: boolean;
}

export type ContinueItem =
  | { kind: 'episode'; episode: Episode; progress: SpokenProgress }
  | { kind: 'book'; book: Audiobook; progress: SpokenProgress };

export type EpisodeFilter = 'all' | 'unplayed' | 'in_progress' | 'downloaded';

const e = encodeURIComponent;

export function spokenApi(c: ApiClient) {
  return {
    podcasts: () => c.get<{ podcasts: Podcast[] }>('/podcasts').then((r) => r.podcasts),
    subscribe: (feedUrl: string) => c.post<Podcast>('/podcasts', { feedUrl }),
    unsubscribe: (id: string) => c.del<void>(`/podcasts/${e(id)}`),
    podcast: (id: string) => c.get<Podcast>(`/podcasts/${e(id)}`),
    patchPodcast: (id: string, b: { speed?: number; skipSilence?: boolean; autoDownload?: boolean; serverKeepLast?: number }) =>
      c.patch<Podcast>(`/podcasts/${e(id)}`, b),
    searchPodcasts: (q: string) => c.get<{ results: PodcastSearchResult[]; errors: string[] }>('/podcasts/search', { q }),
    episodes: (id: string, filter: EpisodeFilter = 'all', offset = 0, limit = 100) =>
      c.get<{ episodes: Episode[] }>(`/podcasts/${e(id)}/episodes`, { filter, offset, limit }).then((r) => r.episodes),
    inbox: (filter: EpisodeFilter = 'unplayed', limit = 50) =>
      c.get<{ episodes: Episode[] }>('/podcasts/episodes', { filter, limit }).then((r) => r.episodes),
    episode: (id: string) => c.get<EpisodeDetail>(`/podcasts/episodes/${e(id)}`),
    refresh: (id: string) => c.post<{ ok: boolean; new?: number; error?: string }>(`/podcasts/${e(id)}/refresh`),
    markShowPlayed: (id: string) => c.post<{ marked: number }>(`/podcasts/${e(id)}/played`),
    books: (sort: 'title' | 'author' | 'added' | 'recent' = 'title') =>
      c.get<{ enabled: boolean; books: Audiobook[] }>('/audiobooks', { sort }),
    book: (id: string) => c.get<AudiobookDetail>(`/audiobooks/${e(id)}`),
    addBookmark: (id: string, positionS: number, note?: string | null) =>
      c.post<Bookmark>(`/audiobooks/${e(id)}/bookmarks`, { positionS, note: note || null }),
    deleteBookmark: (id: string, bid: string) => c.del<void>(`/audiobooks/${e(id)}/bookmarks/${e(bid)}`),
    stations: () => c.get<{ stations: Station[] }>('/stations').then((r) => r.stations),
    searchStations: (q: { q?: string; tag?: string; country?: string }) =>
      c.get<{ results: StationSearchResult[] }>('/stations/search', q).then((r) => r.results),
    addStation: (b: Partial<Omit<StationSearchResult, 'tags'>> & { streamUrl: string; tags?: string[]; probe?: boolean }) =>
      c.post<Station>('/stations', b),
    removeStation: (id: string) => c.del<void>(`/stations/${e(id)}`),
    nowPlaying: (id: string) => c.get<{ stationId: string; title: string | null }>(`/stations/${e(id)}/now-playing`),
    progressSince: (since?: string) => c.get<{ items: SpokenProgress[] }>('/spoken/progress', since ? { since } : undefined),
    putProgress: (items: ProgressWrite[]) =>
      c.put<{ items: { kind: string; id: string; applied: boolean; progress: SpokenProgress | null }[] }>('/spoken/progress', { items }),
    continueListening: () => c.get<{ items: ContinueItem[] }>('/spoken/continue').then((r) => r.items),
    episodeSrc: (id: string) => c.mediaUrl(`/stream/episode/${e(id)}`),
    bookPartSrc: (id: string, part: number) => c.mediaUrl(`/stream/book/${e(id)}/${part}`),
    stationSrc: (id: string) => c.mediaUrl(`/stream/station/${e(id)}`),
    artSrc: (kind: 'podcast' | 'book' | 'station', id: string, size = 256) => c.mediaUrl(`/art/${kind}/${e(id)}?size=${size}`),
  };
}

export interface ProgressWrite {
  kind: 'episode' | 'book';
  id: string;
  positionS?: number;
  durationS?: number;
  played?: boolean;
  speed?: number;
  updatedAt?: string;
}

// ---------- pure helpers (tested) ----------

/** Which part of a multi-file book holds book-wide second `pos`, and the offset inside it. */
export function partAt(parts: Pick<BookPart, 'startS' | 'durationS'>[], pos: number): { index: number; offset: number } {
  if (!parts.length) return { index: 0, offset: 0 };
  let i = 0;
  for (let k = 0; k < parts.length; k++) if (parts[k].startS <= pos) i = k;
  const offset = Math.max(0, Math.min(pos - parts[i].startS, parts[i].durationS || Infinity));
  return { index: i, offset };
}

/** Index of the chapter playing at `pos` (-1 before the first). Chapters sorted by start. */
export function chapterAt(starts: number[], pos: number): number {
  let i = -1;
  for (let k = 0; k < starts.length; k++) if (starts[k] <= pos + 0.25) i = k;
  return i;
}

/** "1:02:03" / "4:05" */
export function clock(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return '–:––';
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = String(t % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** "1 h 5 min left" / "12 min" for list rows. */
export function remaining(p: SpokenProgress | null, duration: number | null): string {
  if (p?.played) return 'Played';
  const d = duration ?? p?.durationS ?? null;
  if (!d) return '';
  const left = Math.max(0, d - (p?.positionS ?? 0));
  const m = Math.round(left / 60);
  const txt = m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.max(1, m)} min`;
  return p && p.positionS > 5 ? `${txt} left` : txt;
}

/**
 * Pending progress writes, coalesced per item (newest wins) so an offline device sends one write per
 * episode/book when it is back. Kept in localStorage by the player.
 */
export class ProgressOutbox {
  items = new Map<string, ProgressWrite>();

  add(w: ProgressWrite): void {
    const k = `${w.kind}:${w.id}`;
    const prev = this.items.get(k);
    this.items.set(k, { ...prev, ...w, updatedAt: w.updatedAt ?? new Date().toISOString() });
  }

  take(): ProgressWrite[] {
    const out = [...this.items.values()];
    this.items.clear();
    return out;
  }

  /** Put back writes that failed, unless a newer one for the same item arrived meanwhile. */
  restore(ws: ProgressWrite[]): void {
    for (const w of ws) {
      const k = `${w.kind}:${w.id}`;
      const cur = this.items.get(k);
      if (!cur || (cur.updatedAt ?? '') < (w.updatedAt ?? '')) this.items.set(k, cur ? { ...w, ...cur } : w);
    }
  }

  toJSON(): ProgressWrite[] {
    return [...this.items.values()];
  }

  static from(json: unknown): ProgressOutbox {
    const o = new ProgressOutbox();
    if (Array.isArray(json)) for (const w of json) if (w && typeof w === 'object' && 'kind' in w && 'id' in w) o.items.set(`${w.kind}:${w.id}`, w as ProgressWrite);
    return o;
  }
}

/** Remove scripts/handlers from feed HTML before showing it (show notes are untrusted). */
export function safeNotes(html: string | null | undefined): string {
  if (!html) return '';
  if (typeof DOMParser === 'undefined') return html.replace(/<[^>]+>/g, ' ');
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll('script,style,iframe,object,embed,form,link,meta,img,video,audio').forEach((n) => n.remove());
  doc.querySelectorAll('*').forEach((el) => {
    for (const a of [...el.attributes]) {
      const n = a.name.toLowerCase();
      if (n.startsWith('on') || n === 'style' || n === 'srcset') el.removeAttribute(a.name);
      else if ((n === 'href' || n === 'src') && !/^https?:/i.test(a.value.trim())) el.removeAttribute(a.name);
    }
    if (el.tagName === 'A') {
      el.setAttribute('target', '_blank');
      el.setAttribute('rel', 'noopener noreferrer');
    }
  });
  return doc.body.innerHTML;
}
