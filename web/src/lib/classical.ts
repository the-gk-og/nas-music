// v9 classical mode (`library.classical`): group an album's tracks by work, show movement names, put the
// composer before the performer.
import type { Track } from './types';

export interface TrackGroup {
  /** null = tracks that are not part of a work. */
  workId: string | null;
  work: string | null;
  composer: string | null;
  tracks: Track[];
  /** Index of the group's first track in the album's track list. */
  start: number;
}

/** Consecutive tracks of the same work form one group; other tracks are grouped as they come. */
export function groupByWork(tracks: Track[]): TrackGroup[] {
  const out: TrackGroup[] = [];
  tracks.forEach((t, i) => {
    const last = out[out.length - 1];
    const wid = t.workId ?? null;
    if (last && last.workId === wid) last.tracks.push(t);
    else out.push({ workId: wid, work: t.work ?? null, composer: t.composer ?? null, tracks: [t], start: i });
  });
  return out;
}

/** True when grouping is worth showing (at least one work). */
export function hasWorks(tracks: Track[]): boolean {
  return tracks.some((t) => !!t.workId);
}

const ROMAN: [number, string][] = [[100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];

export function toRoman(n: number): string {
  let s = '';
  for (const [v, r] of ROMAN)
    while (n >= v) {
      s += r;
      n -= v;
    }
  return s;
}

/** Under a work heading: "II. Andante con moto"; otherwise the title. */
export function movementTitle(t: Track): string {
  if (!t.work || !t.movement) return t.title;
  return t.movementNo ? `${toRoman(t.movementNo)}. ${t.movement}` : t.movement;
}

/** Composer before performer: "Ludwig van Beethoven · Berliner Philharmoniker". */
export function artistLine(t: Track, classical: boolean): string {
  if (!classical || !t.composer || t.composer === t.artist) return t.artist;
  return `${t.composer} · ${t.artist}`;
}
