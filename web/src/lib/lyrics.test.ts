import { describe, expect, it } from 'vitest';
import { currentLineIndex, noLyricsMessage, parseLrc, stampLine, toLrc } from './lyrics';

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
