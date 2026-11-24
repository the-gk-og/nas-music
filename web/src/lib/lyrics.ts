import type { LyricLine, LyricsSource, LyricWord } from './types';

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
const WORD_TS = /<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>/g;

function stampMs(m: RegExpExecArray): number {
  const frac = m[3] ? parseInt(m[3].padEnd(3, '0').slice(0, 3), 10) : 0;
  return parseInt(m[1], 10) * 60000 + parseInt(m[2], 10) * 1000 + frac;
}

/** Enhanced-LRC line body -> plain text and word timings (same rules as the server). */
export function splitWords(body: string, lineMs: number): { text: string; words?: LyricWord[] } {
  const stamps = [...body.matchAll(WORD_TS)];
  if (!stamps.length) return { text: body.trim() };
  const pieces: [number, string][] = [];
  const head = body.slice(0, stamps[0].index);
  if (head.trim()) pieces.push([lineMs, head]);
  stamps.forEach((m, i) => {
    const end = i + 1 < stamps.length ? stamps[i + 1].index : body.length;
    const piece = body.slice(m.index + m[0].length, end);
    if (piece.trim()) pieces.push([stampMs(m as RegExpExecArray), piece]);
  });
  if (!pieces.length) return { text: '' };
  let prev = lineMs;
  const words = pieces.map(([ms, piece], n) => {
    let t = n === 0 ? piece.trimStart() : piece;
    if (n === pieces.length - 1) t = t.trimEnd();
    prev = Math.max(prev, ms);
    return { timeMs: prev, text: t };
  });
  const text = pieces
    .map((p) => p[1])
    .join('')
    .trim();
  return words.length > 1 || words[0].timeMs !== lineMs ? { text, words } : { text };
}

/** Index of the word being sung, or -1 before the first one. */
export function currentWordIndex(words: LyricWord[], positionMs: number): number {
  let ans = -1;
  for (let i = 0; i < words.length && words[i].timeMs <= positionMs; i++) ans = i;
  return ans;
}

/**
 * Parse LRC (or plain text) into display lines, the same way the server does: synced when at least one
 * line has a timestamp; a line with several timestamps appears once per timestamp; `[offset:+/-ms]` applied;
 * enhanced-LRC `<mm:ss.xx>` word stamps become `words`.
 */
export function parseLrc(raw: string): {
  synced: boolean;
  lines: LyricLine[];
  text: string;
} {
  let offset = 0;
  const timed: { timeMs: number; text: string; words?: LyricWord[] }[] = [];
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
      stamps.push(stampMs(m));
      lastEnd = TS.lastIndex;
    }
    if (stamps.length) {
      const { text, words } = splitWords(rawLine.slice(lastEnd), stamps[0]);
      for (const s of stamps) {
        const delta = s - stamps[0];
        timed.push({
          timeMs: s,
          text,
          ...(words
            ? {
                words: words.map((w) => ({
                  timeMs: w.timeMs + delta,
                  text: w.text,
                })),
              }
            : {}),
        });
      }
      plain.push(text);
    } else {
      plain.push(rawLine.trim());
    }
  }
  if (timed.length) {
    // LRC offset: positive = lyrics come earlier.
    const lines = timed
      .map((l) => ({
        timeMs: Math.max(0, l.timeMs - offset),
        text: l.text,
        ...(l.words
          ? {
              words: l.words.map((w) => ({
                timeMs: Math.max(0, w.timeMs - offset),
                text: w.text,
              })),
            }
          : {}),
      }))
      .sort((a, b) => a.timeMs - b.timeMs);
    return {
      synced: true,
      lines,
      text: plain.filter((l) => l !== '').join('\n'),
    };
  }
  while (plain.length && plain[plain.length - 1] === '') plain.pop();
  while (plain.length && plain[0] === '') plain.shift();
  return {
    synced: false,
    lines: plain.map((text) => ({ timeMs: null, text })),
    text: plain.join('\n'),
  };
}

function stamp(ms: number): string {
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const cs = Math.floor((ms % 1000) / 10);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
}

/** Back to LRC text for the editor (synced, keeping word stamps) or plain lines. */
export function toLrc(lines: LyricLine[]): string {
  return lines
    .map((l) => {
      if (l.timeMs == null) return l.text;
      const body = l.words?.length ? l.words.map((w) => `<${stamp(w.timeMs)}>${w.text}`).join('') : l.text;
      return `[${stamp(l.timeMs)}]${body}`;
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

// ---------- timing nudge (per song, kept on this device) ----------

/** Step for the Earlier / Later buttons. */
export const OFFSET_STEP_MS = 250;
const OFFSET_LIMIT_MS = 30_000;
const offsetKey = (trackId: string) => `nm.lyricsOffset.${trackId}`;

/**
 * How far this song's synced lyrics are shifted on this device, in ms. Positive = lines appear later
 * (for lyrics that run ahead of the music), negative = earlier.
 */
export function loadOffset(trackId: string): number {
  try {
    const n = Number(localStorage.getItem(offsetKey(trackId)));
    return Number.isFinite(n) ? clampOffset(n) : 0;
  } catch {
    return 0;
  }
}

export function saveOffset(trackId: string, ms: number): void {
  try {
    if (ms === 0) localStorage.removeItem(offsetKey(trackId));
    else localStorage.setItem(offsetKey(trackId), String(clampOffset(ms)));
  } catch {
    /* storage unavailable: the nudge just lasts for this view */
  }
}

export function clampOffset(ms: number): number {
  return Math.max(-OFFSET_LIMIT_MS, Math.min(OFFSET_LIMIT_MS, Math.round(ms)));
}

/** "+0.5 s" / "−1.25 s" / "In sync". */
export function formatOffset(ms: number): string {
  if (ms === 0) return 'In sync';
  const s = Math.abs(ms) / 1000;
  return `${ms > 0 ? '+' : '−'}${Number(s.toFixed(2))} s`;
}

/** The lyrics with every timestamp moved by `ms`, for saving the nudge into your own LRC copy. */
export function shiftLines(lines: LyricLine[], ms: number): LyricLine[] {
  return lines.map((l) =>
    l.timeMs == null
      ? l
      : {
          ...l,
          timeMs: Math.max(0, l.timeMs + ms),
          ...(l.words
            ? {
                words: l.words.map((w) => ({
                  ...w,
                  timeMs: Math.max(0, w.timeMs + ms),
                })),
              }
            : {}),
        },
  );
}
