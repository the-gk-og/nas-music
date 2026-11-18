// Pure queue operations. The player keeps a local copy of the active queue and mirrors each operation to
// the server (`/queues/{id}/…`); these functions define what the local copy looks like afterwards.
import type { RepeatMode, Track } from './types';

export interface QueueModel {
  id: string;
  name: string;
  slot: number;
  tracks: Track[];
  index: number;
  /** Play order when shuffle is on: a permutation of track indices. */
  order?: number[] | null;
}

export const MAX_QUEUES = 20;

export function totalDurationS(tracks: Track[]): number {
  return tracks.reduce((s, t) => s + (t.durationS || 0), 0);
}

export function clampIndex(i: number, len: number): number {
  return len === 0 ? 0 : Math.max(0, Math.min(len - 1, i));
}

/** Move the item at `from` to `to`, keeping the current track current. */
export function moveTrack(q: QueueModel, from: number, to: number): QueueModel {
  const n = q.tracks.length;
  if (from === to || from < 0 || to < 0 || from >= n || to >= n) return q;
  const tracks = q.tracks.slice();
  const [t] = tracks.splice(from, 1);
  tracks.splice(to, 0, t);
  let index = q.index;
  if (q.index === from) index = to;
  else if (from < q.index && to >= q.index) index--;
  else if (from > q.index && to <= q.index) index++;
  return { ...q, tracks, index, order: null };
}

/** Insert tracks at a position. `next` = right after the current track, `end` = append. */
export function insertTracks(q: QueueModel, add: Track[], position: number | 'next' | 'end'): QueueModel {
  const at =
    position === 'end'
      ? q.tracks.length
      : position === 'next'
        ? Math.min(q.tracks.length, q.index + 1)
        : Math.max(0, Math.min(q.tracks.length, position));
  const tracks = q.tracks.slice();
  tracks.splice(at, 0, ...add);
  const index = q.tracks.length && at <= q.index ? q.index + add.length : q.index;
  return { ...q, tracks, index, order: null };
}

export function insertPosition(q: QueueModel, position: number | 'next' | 'end'): number {
  return position === 'end' ? q.tracks.length : position === 'next' ? Math.min(q.tracks.length, q.index + 1) : position;
}

/** Remove the item at `pos`. Removing the current track makes the following one current. */
export function removeTrack(q: QueueModel, pos: number): QueueModel {
  if (pos < 0 || pos >= q.tracks.length) return q;
  const tracks = q.tracks.slice();
  tracks.splice(pos, 1);
  let index = q.index;
  if (pos < q.index) index--;
  index = clampIndex(index, tracks.length);
  return { ...q, tracks, index, order: null };
}

export type SortKey = 'title' | 'artist' | 'album' | 'duration' | 'reverse';

export function sortQueue(q: QueueModel, key: SortKey): QueueModel {
  const current = q.tracks[q.index];
  let tracks: Track[];
  if (key === 'reverse') tracks = q.tracks.slice().reverse();
  else {
    const get = (t: Track): string | number =>
      key === 'duration' ? t.durationS : (key === 'title' ? t.title : key === 'artist' ? t.artist : t.album).toLowerCase();
    tracks = q.tracks.slice().sort((a, b) => {
      const x = get(a);
      const y = get(b);
      return x < y ? -1 : x > y ? 1 : 0;
    });
  }
  const index = current ? tracks.indexOf(current) : 0;
  return { ...q, tracks, index: Math.max(0, index), order: null };
}

/** Fisher–Yates with injectable RNG; the current track is played first. */
export function shuffleOrder(len: number, current: number, rng: () => number = Math.random): number[] {
  const rest = [...Array(len).keys()].filter((i) => i !== current);
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [rest[i], rest[j]] = [rest[j], rest[i]];
  }
  return len ? [current, ...rest] : [];
}

/**
 * Index of the track that follows `index`, honouring repeat and shuffle order; null at the end.
 * `manual` = user pressed next (repeat-one doesn't hold the track then).
 */
export function nextIndex(q: QueueModel, repeat: RepeatMode, manual: boolean): number | null {
  const n = q.tracks.length;
  if (!n) return null;
  if (repeat === 'one' && !manual) return q.index;
  if (q.order && q.order.length === n) {
    const p = q.order.indexOf(q.index);
    if (p + 1 < n) return q.order[p + 1];
    return repeat !== 'off' ? q.order[0] : null;
  }
  if (q.index + 1 < n) return q.index + 1;
  return repeat !== 'off' ? 0 : null;
}

export function prevIndex(q: QueueModel, repeat: RepeatMode): number | null {
  const n = q.tracks.length;
  if (!n) return null;
  if (q.order && q.order.length === n) {
    const p = q.order.indexOf(q.index);
    if (p > 0) return q.order[p - 1];
    return repeat !== 'off' ? q.order[n - 1] : 0;
  }
  if (q.index > 0) return q.index - 1;
  return repeat !== 'off' ? n - 1 : 0;
}

/** "20. Search: conver" */
export function queueLabel(q: { slot: number; name: string }): string {
  return `${q.slot}. ${q.name}`;
}

/**
 * Musicolet keeps at most 20 queues: a new one takes the first free slot, else replaces the least-recently
 * updated queue that isn't the active one. Returns the slot to use and the queue id it replaces (if any).
 */
export function pickSlot(
  queues: { id: string; slot: number; updatedAt?: string }[],
  activeId: string | null,
): { slot: number; replaces: string | null } {
  const used = new Set(queues.map((q) => q.slot));
  for (let s = 1; s <= MAX_QUEUES; s++) if (!used.has(s)) return { slot: s, replaces: null };
  const candidates = queues.filter((q) => q.id !== activeId).sort((a, b) => (a.updatedAt ?? '').localeCompare(b.updatedAt ?? ''));
  const victim = candidates[0] ?? queues[0];
  return { slot: victim.slot, replaces: victim.id };
}
