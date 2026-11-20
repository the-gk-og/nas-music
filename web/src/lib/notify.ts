// v7: pure helpers for the notification bell and the scrobbling settings (tested in notify.test.ts).
import type { AppNotification, ScrobbleService } from './types';

/** Add or replace notifications by id, newest first, at most `max`. */
export function mergeNotifications(list: AppNotification[], add: AppNotification[], max = 100): AppNotification[] {
  const byId = new Map(list.map((n) => [n.id, n]));
  for (const n of add) byId.set(n.id, { ...byId.get(n.id), ...n });
  return [...byId.values()].sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0)).slice(0, max);
}

export function newestAt(list: AppNotification[]): string | undefined {
  return list.reduce<string | undefined>((m, n) => (!m || n.createdAt > m ? n.createdAt : m), undefined);
}

export const SERVICE_NAMES: Record<string, string> = { listenbrainz: 'ListenBrainz', lastfm: 'Last.fm' };

/** One-line status for a scrobbling service. */
export function scrobbleLine(s: ScrobbleService, when: (iso: string) => string = (x) => new Date(x).toLocaleString()): string {
  if (!s.connected) return s.available ? 'Not connected' : 'Not set up on this server';
  if (s.needsReauth) return `${SERVICE_NAMES[s.service]} rejected the connection — reconnect (${s.pending} waiting)`;
  if (!s.enabled) return `Connected as ${s.username || '?'} · paused`;
  const parts = [`Connected as ${s.username || '?'}`];
  parts.push(s.lastScrobbleAt ? `last scrobble ${when(s.lastScrobbleAt)}` : 'no scrobbles yet');
  if (s.pending) parts.push(`${s.pending} waiting`);
  if (s.failed) parts.push(`${s.failed} failed`);
  return parts.join(' · ');
}
