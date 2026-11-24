import { describe, expect, it } from 'vitest';
import { defaultDeviceName, deviceKind, isTauri, loadJson, preferredFormat, saveJson } from './platform';

const UA = {
  firefoxLinux: 'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0',
  chromeWin: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  edgeWin: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 Edg/131.0',
  safariMac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  chromeAndroid: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36',
  safariIphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1',
  operaChromeOS: 'Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 OPR/114.0',
};

describe('defaultDeviceName', () => {
  it('names browser and OS', () => {
    expect(defaultDeviceName(UA.firefoxLinux)).toBe('Firefox on Linux');
    expect(defaultDeviceName(UA.chromeWin)).toBe('Chrome on Windows');
    expect(defaultDeviceName(UA.edgeWin)).toBe('Edge on Windows');
    expect(defaultDeviceName(UA.safariMac)).toBe('Safari on macOS');
    expect(defaultDeviceName(UA.chromeAndroid)).toBe('Chrome on Android'); // Android before Linux
    expect(defaultDeviceName(UA.safariIphone)).toBe('Safari on iOS'); // iOS before "like Mac OS X"
    expect(defaultDeviceName(UA.operaChromeOS)).toBe('Opera on ChromeOS');
    expect(defaultDeviceName('')).toBe('Browser on unknown OS');
  });
});

describe('outside a browser (tests, SSR)', () => {
  it('is a web device, not Tauri, and asks for Opus', () => {
    expect(isTauri()).toBe(false);
    expect(deviceKind()).toBe('web');
    expect(preferredFormat()).toBe('opus');
  });
  it('loadJson falls back and saveJson is a no-op without storage', () => {
    saveJson('k', { a: 1 });
    expect(loadJson('k', { a: 0, b: 2 })).toEqual({ a: 0, b: 2 });
  });
});
