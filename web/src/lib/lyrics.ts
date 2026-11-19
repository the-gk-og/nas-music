import type { LyricLine, LyricsSource } from './types';

/** Index of the line being sung at `positionMs`, or -1 before the first timestamp / for unsynced lyrics. */
export function currentLineIndex(lines: LyricLine[], positionMs: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const t = lines[mid].timeMs;
    if (t == null) return -1;
    if (t <= positionMs) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

const TS = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;

/**
 * Parse LRC (or plain text) into display lines, the same way the server does: synced when at least one
 * line has a timestamp; a line with several timestamps appears once per timestamp; `[offset:+/-ms]` applied.
 */
export function parseLrc(raw: string): { synced: boolean; lines: LyricLine[]; text: string } {
  let offset = 0;
  const timed: LyricLine[] = [];
  const plain: string[] = [];
  for (const rawLine of raw.replace(/\r\n?/g, '\n').split('\n')) {
    const off = /^\[offset:\s*([+-]?\d+)\s*\]$/i.exec(rawLine.trim());
    if (off) {
      offset = parseInt(off[1], 10);
      continue;
    }
    if (/^\[(ar|ti|al|by|au|length|re|ve|#):/i.test(rawLine.trim())) continue;
    TS.lastIndex = 0;
    const stamps: number[] = [];
    let m: RegExpExecArray | null;
    let lastEnd = 0;
    while ((m = TS.exec(rawLine)) && m.index === lastEnd) {
      const frac = m[3] ? parseInt(m[3].padEnd(3, '0').slice(0, 3), 10) : 0;
      stamps.push(parseInt(m[1], 10) * 60000 + parseInt(m[2], 10) * 1000 + frac);
      lastEnd = TS.lastIndex;
    }
    const text = rawLine.slice(lastEnd).trim();
    if (stamps.length) {
      for (const s of stamps) timed.push({ timeMs: s, text });
      plain.push(text);
    } else {
      plain.push(rawLine.trim());
    }
  }
  if (timed.length) {
    // LRC offset: positive = lyrics come earlier.
    const lines = timed
      .map((l) => ({ timeMs: Math.max(0, (l.timeMs ?? 0) - offset), text: l.text }))
      .sort((a, b) => a.timeMs - b.timeMs);
    return { synced: true, lines, text: plain.filter((l) => l !== '').join('\n') };
  }
  while (plain.length && plain[plain.length - 1] === '') plain.pop();
  while (plain.length && plain[0] === '') plain.shift();
  return { synced: false, lines: plain.map((text) => ({ timeMs: null, text })), text: plain.join('\n') };
}

/** Back to LRC text for the editor (synced) or plain lines. */
export function toLrc(lines: LyricLine[]): string {
  return lines
    .map((l) => {
      if (l.timeMs == null) return l.text;
      const m = Math.floor(l.timeMs / 60000);
      const s = Math.floor((l.timeMs % 60000) / 1000);
      const cs = Math.floor((l.timeMs % 1000) / 10);
      return `[${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}]${l.text}`;
    })
    .join('\n');
}

/** Stamp `line` with the current position (LRC editor "tap to sync" helper). */
export function stampLine(raw: string, lineNo: number, positionMs: number): string {
  const lines = raw.split('\n');
  if (lineNo < 0 || lineNo >= lines.length) return raw;
  const text = lines[lineNo].replace(/^(\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+/, '');
  const [stamped] = toLrc([{ timeMs: positionMs, text }]).split('\n');
  lines[lineNo] = stamped;
  return lines.join('\n');
}

export function noLyricsMessage(fileName: string): string {
  return `Could not find embedded lyrics in tags of "${fileName}" file.`;
}

export const SOURCE_LABEL: Record<LyricsSource, string> = {
  embedded: 'Embedded lyrics',
  lrc_file: '.lrc file',
  user: 'Your edit',
  lrclib: 'LRCLIB',
};

/** Attribution line shown under lyrics that came from an online source. */
export function lyricsAttribution(source: LyricsSource | undefined): string | null {
  return source === 'lrclib' ? 'Lyrics from LRCLIB' : null;
}
