import { describe, expect, it } from 'vitest';
import { RETRY_MS, retryDelay } from './pwa.svelte';

describe('offline retry', () => {
  it('backs off and settles at a minute', () => {
    expect(retryDelay(0)).toBe(3000);
    expect(retryDelay(2)).toBe(10000);
    expect(retryDelay(99)).toBe(60000);
    expect(retryDelay(-1)).toBe(RETRY_MS[0]);
  });
});
