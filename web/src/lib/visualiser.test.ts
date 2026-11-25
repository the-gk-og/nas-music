import { describe, expect, it } from 'vitest';
import { barLevels, bandEdges } from './visualiser';

describe('visualiser', () => {
  it('spaces bands logarithmically and gives every band a bin', () => {
    const e = bandEdges(48, 1024, 48000);
    expect(e).toHaveLength(49);
    for (let i = 1; i < e.length; i++) expect(e[i]).toBeGreaterThan(e[i - 1]);
    // the first bars cover much narrower ranges than the last ones
    expect(e[1] - e[0]).toBeLessThan(e[48] - e[47]);
    expect(e[48]).toBeLessThanOrEqual(1024);
  });
  it('takes each band peak, scaled to 0..1', () => {
    const freq = new Uint8Array(16);
    freq[2] = 255;
    freq[9] = 51;
    expect(barLevels(freq, [0, 4, 8, 16])).toEqual([1, 0, 0.2]);
  });
  it('lets bars fall gradually', () => {
    const silent = new Uint8Array(8);
    expect(barLevels(silent, [0, 4, 8], [1, 0.05], 0.1).map((x) => +x.toFixed(2))).toEqual([0.9, 0]);
  });
});
