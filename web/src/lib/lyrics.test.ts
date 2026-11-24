import { describe, expect, it } from 'vitest';
import {
  clampOffset,
  currentLineIndex,
  currentWordIndex,
  formatOffset,
  loadOffset,
  noLyricsMessage,
  parseLrc,
  saveOffset,
  shiftLines,
  stampLine,
  toLrc,
} from './lyrics';

const lines = [
  { timeMs: 1000, text: 'one' },
  { timeMs: 5000, text: 'two' },
  { timeMs: 5000, text: 'two-b' },
  { timeMs: 9000, text: 'three' },
];

describe('currentLineIndex', () => {
  it('is -1 before the first line', () => {
    expect(currentLineIndex(lines, 0)).toBe(-1);
    expect(currentLineIndex(lines, 999)).toBe(-1);
  });
  it('selects the last line whose time has passed', () => {
    expect(currentLineIndex(lines, 1000)).toBe(0);
    expect(currentLineIndex(lines, 4999)).toBe(0);
    expect(currentLineIndex(lines, 5000)).toBe(2);
    expect(currentLineIndex(lines, 8999)).toBe(2);
    expect(currentLineIndex(lines, 9000)).toBe(3);
    expect(currentLineIndex(lines, 1e9)).toBe(3);
  });
  it('is -1 for unsynced or empty lyrics', () => {
    expect(currentLineIndex([{ timeMs: null, text: 'a' }], 5000)).toBe(-1);
    expect(currentLineIndex([], 5000)).toBe(-1);
  });
});

describe('parseLrc', () => {
  it('parses timestamps, repeats and offset', () => {
    const p = parseLrc('[ti:x]\n[offset:500]\n[00:01.50]a\n[00:03.00][00:10.00]chorus\n[00:05]b');
    expect(p.synced).toBe(true);
    expect(p.lines).toEqual([
      { timeMs: 1000, text: 'a' },
      { timeMs: 2500, text: 'chorus' },
      { timeMs: 4500, text: 'b' },
      { timeMs: 9500, text: 'chorus' },
    ]);
  });
  it('treats text without timestamps as plain', () => {
    const p = parseLrc('\nhello\n\nworld\n');
    expect(p.synced).toBe(false);
    expect(p.lines.map((l) => l.text)).toEqual(['hello', '', 'world']);
  });
  it('round-trips through toLrc', () => {
    const p = parseLrc('[01:02.34]x\n[00:00.00]y');
    expect(parseLrc(toLrc(p.lines)).lines).toEqual(p.lines);
  });
  it('stamps a line for the sync editor', () => {
    expect(stampLine('a\n[00:01.00]b', 1, 62340)).toBe('a\n[01:02.34]b');
  });
});

it('no-lyrics message matches the spec', () => {
  expect(noLyricsMessage('Conversations with Strangers.flac')).toBe(
    'Could not find embedded lyrics in tags of "Conversations with Strangers.flac" file.',
  );
});

it('attributes LRCLIB lyrics', async () => {
  const { lyricsAttribution, SOURCE_LABEL } = await import('./lyrics');
  expect(lyricsAttribution('lrclib')).toBe('Lyrics from LRCLIB');
  expect(lyricsAttribution('embedded')).toBeNull();
  expect(SOURCE_LABEL.lrclib).toBe('LRCLIB');
});

describe('timing nudge', () => {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
    },
  });

  it('formats the offset', () => {
    expect(formatOffset(0)).toBe('In sync');
    expect(formatOffset(500)).toBe('+0.5 s');
    expect(formatOffset(-1250)).toBe('−1.25 s');
  });
  it('clamps to ±30 s', () => {
    expect(clampOffset(99_999)).toBe(30_000);
    expect(clampOffset(-99_999)).toBe(-30_000);
  });
  it('remembers per track and forgets at zero', () => {
    saveOffset('t1', 750);
    expect(loadOffset('t1')).toBe(750);
    expect(loadOffset('t2')).toBe(0);
    saveOffset('t1', 0);
    expect(loadOffset('t1')).toBe(0);
  });
  it('shifts timestamps, never below zero, leaving untimed lines alone', () => {
    const out = shiftLines(
      [
        { timeMs: 100, text: 'a' },
        { timeMs: 2000, text: 'b' },
        { timeMs: null, text: 'c' },
      ],
      -500,
    );
    expect(out.map((l) => l.timeMs)).toEqual([0, 1500, null]);
  });
});

describe('word-by-word (enhanced LRC)', () => {
  it('parses word timings like the server', () => {
    const p = parseLrc('[00:12.00]<00:12.00>Hello <00:12.50>big <00:13.00>world <00:13.80>\n[00:15.00]plain');
    expect(p.lines[0].text).toBe('Hello big world');
    expect(p.lines[0].words).toEqual([
      { timeMs: 12000, text: 'Hello ' },
      { timeMs: 12500, text: 'big ' },
      { timeMs: 13000, text: 'world' },
    ]);
    expect(p.lines[1].words).toBeUndefined();
  });
  it('applies offset and repeats to words', () => {
    const p = parseLrc('[offset:+500]\n[00:10.00][00:30.00]Oh <00:11.00>yeah');
    expect(p.lines[1].words).toEqual([
      { timeMs: 29500, text: 'Oh ' },
      { timeMs: 30500, text: 'yeah' },
    ]);
  });
  it('round-trips through toLrc so editing keeps word timings', () => {
    const lines = parseLrc('[00:12.00]<00:12.00>Hello <00:12.50>world').lines;
    expect(parseLrc(toLrc(lines)).lines).toEqual(lines);
  });
  it('finds the current word', () => {
    const w = [
      { timeMs: 100, text: 'a ' },
      { timeMs: 500, text: 'b' },
    ];
    expect(currentWordIndex(w, 50)).toBe(-1);
    expect(currentWordIndex(w, 499)).toBe(0);
    expect(currentWordIndex(w, 900)).toBe(1);
  });
  it('shifts words with the nudge', () => {
    const [l] = shiftLines(
      [
        {
          timeMs: 1000,
          text: 'a b',
          words: [
            { timeMs: 1000, text: 'a ' },
            { timeMs: 1400, text: 'b' },
          ],
        },
      ],
      250,
    );
    expect(l.words?.map((w) => w.timeMs)).toEqual([1250, 1650]);
  });
});
