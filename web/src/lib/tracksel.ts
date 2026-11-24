// Pure helpers for picking several songs at once and for duplicate checks.
import type { Track } from './types';

/** Songs that are / aren't already in a list (by id). */
export function splitDuplicates(tracks: Track[], have: Set<string>): { dup: Track[]; fresh: Track[] } {
  const dup: Track[] = [];
  const fresh: Track[] = [];
  for (const t of tracks) (have.has(t.id) ? dup : fresh).push(t);
  return { dup, fresh };
}

/** Add or remove one song, keeping the order songs were picked in. */
export function toggleTrack(sel: Track[], t: Track): Track[] {
  return sel.some((x) => x.id === t.id) ? sel.filter((x) => x.id !== t.id) : [...sel, t];
}

/** Shift-click: add every song between `from` and `to` (inclusive) in `list`, in list order. */
export function addRange(sel: Track[], list: Track[], from: number, to: number): Track[] {
  const [a, b] = from <= to ? [from, to] : [to, from];
  const ids = new Set(sel.map((t) => t.id));
  const out = sel.slice();
  for (const t of list.slice(Math.max(0, a), b + 1)) {
    if (!ids.has(t.id)) {
      ids.add(t.id);
      out.push(t);
    }
  }
  return out;
}
