import { describe, expect, it } from 'vitest';
import { ApiClient, isSameOrigin, normaliseServerAddress } from './client';

describe('server addresses with custom ports (relay readiness)', () => {
  it('keeps non-default ports and drops paths', () => {
    expect(normaliseServerAddress('music.whoactuallyneeds.men:8443')).toBe('https://music.whoactuallyneeds.men:8443');
    expect(normaliseServerAddress('https://music.whoactuallyneeds.men:8443/')).toBe('https://music.whoactuallyneeds.men:8443');
    expect(normaliseServerAddress(' 192.168.68.2:8443 ')).toBe('https://192.168.68.2:8443');
    expect(normaliseServerAddress('http://nas.local:8080/api/v1')).toBe('http://nas.local:8080');
    expect(normaliseServerAddress('https://music.example.com:443')).toBe('https://music.example.com');
  });
  it('compares origins including the port', () => {
    expect(isSameOrigin('https://music.example.com:8443', 'https://music.example.com')).toBe(false);
    expect(isSameOrigin('https://music.example.com:8443', 'https://music.example.com:8443')).toBe(true);
    expect(isSameOrigin('', 'https://x')).toBe(true);
  });
  it('builds API, media and Connect URLs on the same port', async () => {
    const c = new ApiClient({ origin: 'https://music.whoactuallyneeds.men:8443', mode: 'bearer', token: 'abc', fetchImpl: (() => {}) as unknown as typeof fetch });
    expect(c.base).toBe('https://music.whoactuallyneeds.men:8443/api/v1');
    expect(c.connectUrl).toBe('wss://music.whoactuallyneeds.men:8443/api/v1/connect?token=abc');
    expect(await c.streamSrc('t1')).toBe('https://music.whoactuallyneeds.men:8443/api/v1/stream/t1?format=opus&token=abc');
    expect(await c.artSrc('al1', 256)).toBe('https://music.whoactuallyneeds.men:8443/api/v1/art/al1?size=256&token=abc');
    const lan = new ApiClient({ origin: 'http://192.168.68.2:8080', mode: 'bearer', token: 't', fetchImpl: (() => {}) as unknown as typeof fetch });
    expect(lan.connectUrl).toBe('ws://192.168.68.2:8080/api/v1/connect?token=t');
  });
});
