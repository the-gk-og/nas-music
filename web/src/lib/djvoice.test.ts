import { describe, expect, it } from 'vitest';
import { DUCK_LEVEL, djEligible, spotKey } from './djvoice.svelte';

describe('radio DJ voice', () => {
  it('talks only in radio and mixes', () => {
    expect(djEligible({ radio: true }, undefined)).toBe(true);
    expect(djEligible({}, 'mix')).toBe(true);
    expect(djEligible({}, 'more_like')).toBe(true);
    expect(djEligible({}, 'library')).toBe(false);
    expect(djEligible({}, undefined)).toBe(false);
    expect(djEligible(null, 'mix')).toBe(false);
  });

  it('keys intros by spot and neighbours', () => {
    const a = spotKey({ queueId: 'q', position: 3, trackId: 't3', previousTrackId: 't2' });
    expect(a).toBe('q|3|t3|t2');
    expect(spotKey({ queueId: 'q', position: 3, trackId: 't3', previousTrackId: null })).not.toBe(a);
  });

  it('ducks the music, not mutes it', () => {
    expect(DUCK_LEVEL).toBeGreaterThan(0);
    expect(DUCK_LEVEL).toBeLessThan(0.5);
  });
});
