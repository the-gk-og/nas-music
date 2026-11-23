import { describe, expect, it } from 'vitest';
import {
  confidenceClass,
  confidenceText,
  countAbove,
  etaText,
  filterLogs,
  filterWants,
  groupChanges,
  qualityText,
  uptimeText,
  validateBaseUrl,
  validateCacheGb,
  validateTimezone,
  type AutotagField,
  type AutotagGroup,
  type AutotagSuggestion,
  type AdminWantItem,
  type LogEntry,
} from './admin';

describe('validateBaseUrl', () => {
  it('accepts hosts with custom ports (relay)', () => {
    expect(validateBaseUrl('https://music.whoactuallyneeds.men:8443')).toEqual({ ok: true, value: 'https://music.whoactuallyneeds.men:8443' });
    expect(validateBaseUrl('192.168.68.2:8443')).toEqual({ ok: true, value: 'https://192.168.68.2:8443' });
    expect(validateBaseUrl('https://music.example.com/')).toEqual({ ok: true, value: 'https://music.example.com' });
    expect(validateBaseUrl('https://music.example.com:443')).toEqual({ ok: true, value: 'https://music.example.com' });
  });
  it('rejects paths, bad schemes, bad ports and credentials', () => {
    expect(validateBaseUrl('https://x.com/music').ok).toBe(false);
    expect(validateBaseUrl('ftp://x.com').ok).toBe(false);
    expect(validateBaseUrl('https://x.com:99999').ok).toBe(false);
    expect(validateBaseUrl('https://u:p@x.com').ok).toBe(false);
    expect(validateBaseUrl('not a url').ok).toBe(false);
  });
  it('treats empty as unset unless required', () => {
    expect(validateBaseUrl('  ')).toEqual({ ok: true, value: null });
    expect(validateBaseUrl('', true).ok).toBe(false);
  });
});

it('validates time zones and cache size', () => {
  expect(validateTimezone('Europe/London')).toBeNull();
  expect(validateTimezone('Mars/Olympus')).not.toBeNull();
  expect(validateTimezone('UTC', ['UTC'])).toBeNull();
  expect(validateCacheGb(50)).toBeNull();
  expect(validateCacheGb(0)).not.toBeNull();
  expect(validateCacheGb(NaN)).not.toBeNull();
});

it('formats ETA and uptime', () => {
  expect(etaText(null)).toBe('—');
  expect(etaText(30)).toBe('about a minute');
  expect(etaText(1800)).toBe('about 30 min');
  expect(etaText(5 * 3600)).toBe('about 5 h');
  expect(uptimeText(3 * 86400 + 4 * 3600 + 60)).toBe('3d 4h');
  expect(uptimeText(125)).toBe('2m');
});

it('filters logs by level and text', () => {
  const e = (seq: number, level: string, message: string): LogEntry => ({ seq, at: '', level, logger: 'nasmusic.app', message });
  const logs = [e(1, 'DEBUG', 'cache hit'), e(2, 'INFO', 'scan done'), e(3, 'WARNING', 'slow scan'), e(4, 'ERROR', 'decode failed')];
  expect(filterLogs(logs, 'WARNING', '').map((x) => x.seq)).toEqual([3, 4]);
  expect(filterLogs(logs, 'DEBUG', 'scan').map((x) => x.seq)).toEqual([2, 3]);
  expect(filterLogs(logs, 'INFO', 'NASMUSIC').length).toBe(3);
});

describe('tag fixing helpers', () => {
  const sug = (id: string, confidence: number, status: 'pending' | 'accepted' = 'pending', changes: AutotagField[] = []) =>
    ({
      trackId: id,
      path: id,
      status,
      confidence,
      current: {},
      suggested: {},
      changes,
      defaultFields: changes,
    }) as AutotagSuggestion;
  const group = (tracks: AutotagSuggestion[]) =>
    ({
      groupId: 'g',
      albumId: 'a',
      currentAlbum: null,
      currentAlbumArtist: null,
      albumHasArt: false,
      confidence: 0.9,
      albumTrackCount: tracks.length,
      release: null,
      tracks,
    }) as AutotagGroup;

  it('formats and bands confidence', () => {
    expect(confidenceText(0.876)).toBe('88%');
    expect(confidenceText(1.2)).toBe('100%');
    expect(confidenceClass(0.9)).toBe('ok');
    expect(confidenceClass(0.7)).toBe('warnc');
    expect(confidenceClass(0.3)).toBe('bad');
  });
  it('counts pending tracks above a threshold', () => {
    const gs = [group([sug('a', 0.95), sug('b', 0.8), sug('c', 0.99, 'accepted')]), group([sug('d', 0.9)])];
    expect(countAbove(gs, 0.9)).toBe(2);
    expect(countAbove(gs, 0.5)).toBe(3);
  });
  it('lists group changes in display order', () => {
    expect(groupChanges(group([sug('a', 1, 'pending', ['year', 'title']), sug('b', 1, 'pending', ['album', 'title'])]))).toEqual(['title', 'album', 'year']);
  });
  it('describes copy quality', () => {
    expect(
      qualityText({
        codec: 'FLAC',
        bitrate: 900,
        sampleRate: 44100,
        bitsPerSample: 16,
      }),
    ).toBe('FLAC · 16-bit/44.1 kHz · 900 kbps');
    expect(
      qualityText({
        codec: 'MP3',
        bitrate: 320,
        sampleRate: 48000,
        bitsPerSample: null,
      }),
    ).toBe('MP3 · 48.0 kHz · 320 kbps');
  });
});

describe('filterWants', () => {
  const w = (id: string, o: Partial<AdminWantItem>): AdminWantItem => ({
    id, type: 'album', title: 'T', artist: 'A', status: 'want', userId: 'u1', username: 'sam', done: false, suggestDone: false, ...o,
  });
  const items = [
    w('1', { title: 'Paper Lanterns', note: 'deluxe' }),
    w('2', { type: 'track', title: 'Night Bus', done: true }),
    w('3', { userId: 'u2', username: 'kiddo', artist: 'The Kites' }),
  ];
  const ids = (f: Parameters<typeof filterWants>[1]) => filterWants(items, f).map((i) => i.id);
  it('filters by user, type and done', () => {
    expect(ids({})).toEqual(['1', '2', '3']);
    expect(ids({ userId: 'u2' })).toEqual(['3']);
    expect(ids({ type: 'track' })).toEqual(['2']);
    expect(ids({ hideDone: true })).toEqual(['1', '3']);
  });
  it('searches title, artist, note and username', () => {
    expect(ids({ q: 'DELUXE' })).toEqual(['1']);
    expect(ids({ q: 'kites' })).toEqual(['3']);
    expect(ids({ q: 'kiddo' })).toEqual(['3']);
    expect(ids({ q: '  ' })).toEqual(['1', '2', '3']);
  });
});
