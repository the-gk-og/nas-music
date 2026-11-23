// "Resume exactly where you left off" for the web player and desktop app: which queue, which song and where
// to put the player when the page (re)opens. Pure functions, unit-tested in resume.test.ts.
import type { PlaybackState, Queue } from './types';

export interface SavedSpot {
  queueId: string | null;
  index: number;
  positionS: number;
  /** id of the song at `index` when saved (finds it again if the queue changed meanwhile) */
  trackId?: string | null;
  /** ms since epoch */
  savedAt?: number;
}

/** The queue to reopen: the one this browser last played, else the server's most recently used one. */
export function resumeQueueId(queues: Pick<Queue, 'id' | 'updatedAt'>[], saved: SavedSpot | null): string | null {
  if (saved?.queueId && queues.some((q) => q.id === saved.queueId)) return saved.queueId;
  let best: Pick<Queue, 'id' | 'updatedAt'> | null = null;
  for (const q of queues) if (!best || (q.updatedAt ?? '') > (best.updatedAt ?? '')) best = q;
  return best?.id ?? null;
}

/**
 * Index + position to open a queue at. This browser's own spot wins for its own queue (it is fresher than the
 * server's copy, which is patched every 15 s); otherwise the server's saved index and position. Never the top
 * of the list unless that is where it was.
 */
export function resumeSpot(
  q: { id: string; tracks: { id: string }[]; index: number; positionMs?: number },
  saved: SavedSpot | null,
): { index: number; positionS: number } {
  const n = q.tracks.length;
  if (!n) return { index: 0, positionS: 0 };
  if (saved && saved.queueId === q.id) {
    let i = saved.index;
    if (saved.trackId && q.tracks[i]?.id !== saved.trackId) {
      const found = q.tracks.findIndex((t) => t.id === saved.trackId);
      if (found >= 0) i = found;
      else return { index: Math.min(Math.max(0, i), n - 1), positionS: 0 };
    }
    if (i >= 0 && i < n) return { index: i, positionS: Math.max(0, saved.positionS) };
  }
  return { index: Math.min(Math.max(0, q.index), n - 1), positionS: Math.max(0, (q.positionMs ?? 0) / 1000) };
}

/**
 * Adopt what another device last played instead of this browser's own spot? Only when it is newer, paused
 * (playing elsewhere = this browser is a remote), and was a queue or a spoken item.
 */
export function preferServer(localSavedAt: number | undefined | null, s: PlaybackState | null, myDeviceId: string | null, slackMs = 5000): boolean {
  if (!s || s.playing) return false;
  if (!s.queueId && !s.media) return false;
  if (s.activeDeviceId && s.activeDeviceId === myDeviceId) return false;
  const at = Date.parse(s.positionAt);
  if (!isFinite(at)) return false;
  return localSavedAt == null || at > localSavedAt + slackMs;
}
