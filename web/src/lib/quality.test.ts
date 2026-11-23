import { describe, expect, it } from 'vitest';
import { isLanHost, netKind, originalLabel, QUALITIES, resolveQuality, specLabel, streamQuery } from './quality';

describe('stream quality', () => {
  it('detects home vs remote from the address in use', () => {
    const lan = 'https://192.168.68.2:8443';
    const tun = 'https://music.example.com';
    expect(netKind('', 'https://192.168.68.2:8443', lan, tun)).toBe('home');
    expect(netKind('https://music.example.com', 'tauri://localhost', lan, tun)).toBe('remote');
    expect(netKind('', 'https://music.example.com', lan, tun)).toBe('remote');
    expect(netKind('https://nas.local:8443', '', null, null)).toBe('home');
    expect(netKind('https://relay.example.org:8443', '', null, null)).toBe('remote');
    // The tunnel address wins even if it happens to be a private-looking host.
    expect(netKind('https://10.0.0.5', '', null, 'https://10.0.0.5')).toBe('remote');
  });

  it('knows private hosts', () => {
    for (const h of ['localhost', '127.0.0.1', '10.1.2.3', '172.16.0.1', '172.31.9.9', '192.168.1.1', 'nas.local', 'box.lan', '[fd00::1]']) expect(isLanHost(h)).toBe(true);
    for (const h of ['172.32.0.1', '8.8.8.8', 'music.example.com']) expect(isLanHost(h)).toBe(false);
  });

  it('resolves auto: lossless at home, Opus 128 remote; Safari always lossless', () => {
    expect(resolveQuality('auto', 'home')).toEqual({ format: 'flac', kbps: 0 });
    expect(resolveQuality('auto', 'remote')).toEqual({ format: 'opus', kbps: 128 });
    expect(resolveQuality('opus256', 'home')).toEqual({ format: 'opus', kbps: 256 });
    expect(resolveQuality('lossless', 'remote')).toEqual({ format: 'flac', kbps: 0 });
    expect(resolveQuality('opus96', 'remote', false)).toEqual({ format: 'flac', kbps: 0 });
    expect(QUALITIES.map((q) => q.key)).toEqual(['auto', 'lossless', 'opus64', 'opus96', 'opus128', 'opus160', 'opus192', 'opus256']);
  });

  it('builds queries and labels', () => {
    expect(streamQuery({ format: 'flac', kbps: 0 })).toBe('format=flac');
    expect(streamQuery({ format: 'opus', kbps: 96 })).toBe('format=opus&bitrate=96');
    expect(specLabel({ format: 'opus', kbps: 128 })).toBe('Opus 128');
    expect(originalLabel('FLAC', 16, 44100, 900)).toBe('FLAC 16/44.1');
    expect(originalLabel('FLAC', 24, 96000)).toBe('FLAC 24/96');
    expect(originalLabel('MP3', null, 44100, 320)).toBe('MP3 320');
    expect(originalLabel(null)).toBe('Original');
  });
});
