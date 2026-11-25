import { describe, expect, it } from 'vitest';
import { fmtBytes, fmtTime, formatLine, tabTitle, unknown } from './format';

describe('fmtTime', () => {
  it('formats m:ss and h:mm:ss', () => {
    expect(fmtTime(0)).toBe('0:00');
    expect(fmtTime(59.9)).toBe('0:59');
    expect(fmtTime(61)).toBe('1:01');
    expect(fmtTime(3600)).toBe('1:00:00');
    expect(fmtTime(3725)).toBe('1:02:05');
  });
  it('treats missing, negative and non-finite as zero', () => {
    for (const v of [null, undefined, -5, NaN, Infinity]) expect(fmtTime(v)).toBe('0:00');
  });
});

describe('fmtBytes', () => {
  it('picks a unit and one decimal above bytes', () => {
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(1536)).toBe('1.5 KB');
    expect(fmtBytes(5 * 1024 ** 2)).toBe('5.0 MB');
    expect(fmtBytes(3 * 1024 ** 4)).toBe('3072.0 GB');
  });
  it('says unknown for nothing', () => {
    expect(fmtBytes(0)).toBe('[unknown]');
    expect(fmtBytes(null)).toBe('[unknown]');
  });
});

describe('unknown', () => {
  it('keeps real values, including 0 and false', () => {
    expect(unknown(0)).toBe('0');
    expect(unknown(false)).toBe('false');
    expect(unknown('x')).toBe('x');
    expect(unknown('')).toBe('[unknown]');
    expect(unknown(null)).toBe('[unknown]');
  });
});

describe('formatLine', () => {
  it('builds the Now Playing format line', () => {
    expect(formatLine({ codec: 'flac', bitsPerSample: 16, sampleRate: 44100, bitrateKbps: 912.4 })).toBe('FLAC · 16-bit · 44.1 kHz · 912 kbps');
    expect(formatLine({ codec: 'opus', sampleRate: 48000, bitrateKbps: 128 })).toBe('OPUS · 48 kHz · 128 kbps');
  });
  it('mentions channels only when not stereo', () => {
    expect(formatLine({ codec: 'mp3', channels: 2 })).toBe('MP3');
    expect(formatLine({ codec: 'mp3', channels: 1 })).toBe('MP3 · mono');
    expect(formatLine({ codec: 'flac', channels: 6 })).toBe('FLAC · 6 ch');
  });
  it('says unknown when nothing is known', () => {
    expect(formatLine({})).toBe('[unknown]');
  });
});

describe('tabTitle', () => {
  it('shows the song while something is loaded', () => {
    expect(tabTitle({ title: 'Song', artist: 'Band' }, true)).toBe('▶ Song · Band — Homebeat');
    expect(tabTitle({ title: 'Song', artist: null }, false)).toBe('Song — Homebeat');
    expect(tabTitle(null, false)).toBe('Homebeat');
  });
});
