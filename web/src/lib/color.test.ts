import { describe, expect, it } from 'vitest';
import { dominantColor, dominantFromPixels, tint } from './color';

const px = (...rgba: number[][]) => rgba.flat();

describe('dominantFromPixels', () => {
  it('returns the colour of a flat image', () => {
    expect(dominantFromPixels(px([200, 30, 30, 255], [200, 30, 30, 255]))).toEqual([200, 30, 30]);
  });
  it('ignores transparent pixels', () => {
    expect(dominantFromPixels(px([0, 0, 255, 0], [10, 200, 10, 255]))).toEqual([10, 200, 10]);
  });
  it('prefers a saturated colour over grey', () => {
    const [r, g, b] = dominantFromPixels(px([128, 128, 128, 255], [128, 128, 128, 255], [220, 20, 20, 255]));
    expect(r).toBeGreaterThan(g + 50);
    expect(r).toBeGreaterThan(b + 50);
  });
  it('falls back to dark grey for an empty or fully transparent image', () => {
    expect(dominantFromPixels([])).toEqual([40, 40, 40]);
    expect(dominantFromPixels(px([255, 0, 0, 0]))).toEqual([40, 40, 40]);
  });
});

describe('tint', () => {
  it('darkens by the factor', () => {
    expect(tint([200, 100, 50])).toBe('rgb(90, 45, 23)');
    expect(tint([200, 100, 50], 1)).toBe('rgb(200, 100, 50)');
  });
});

describe('dominantColor', () => {
  it('is null outside a browser', async () => {
    expect(await dominantColor('x.jpg')).toBeNull();
  });
});
