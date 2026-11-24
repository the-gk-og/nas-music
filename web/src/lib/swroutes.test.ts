import { describe, expect, it } from 'vitest';
import { precacheList, strategyFor } from './swroutes';

const O = 'https://music.example.org';
const s = (path: string, mode = 'cors', method = 'GET', origin = O) => strategyFor(new URL(path, origin), O, method, mode);

describe('service worker routing', () => {
  it('never touches the API, streams, admin pages or other origins', () => {
    expect(s('/api/v1/tracks/abc/stream')).toBe('skip');
    expect(s('/api/v1/health', 'navigate')).toBe('skip');
    expect(s('/admin', 'navigate')).toBe('skip');
    expect(s('/admin/pair', 'navigate')).toBe('skip');
    expect(s('/assets/x.js', 'cors', 'GET', 'https://other.example')).toBe('skip');
    expect(s('/assets/x.js', 'cors', 'POST')).toBe('skip');
    expect(s('/sw.js')).toBe('skip');
  });
  it('serves navigations from the shell, the Jam page from its own', () => {
    expect(s('/', 'navigate')).toBe('shell');
    expect(s('/join/WELCOME1', 'navigate')).toBe('shell');
    expect(s('/jam/ABCD', 'navigate')).toBe('jam-shell');
    expect(s('/administrator', 'navigate')).toBe('shell');
  });
  it('caches hashed assets forever and refreshes the rest', () => {
    expect(s('/assets/index-abc123.js')).toBe('cache-first');
    expect(s('/manifest.webmanifest')).toBe('stale-while-revalidate');
    expect(s('/icons/icon-192.png')).toBe('stale-while-revalidate');
  });
  it('precaches the build minus source maps, plus the public files', () => {
    const l = precacheList(['index.html', 'jam.html', 'assets/index-1.js', 'assets/index-1.js.map', 'sw.js']);
    expect(l).toContain('/index.html');
    expect(l).toContain('/jam.html');
    expect(l).toContain('/assets/index-1.js');
    expect(l).toContain('/manifest.webmanifest');
    expect(l).not.toContain('/assets/index-1.js.map');
    expect(l).not.toContain('/sw.js');
  });
});
