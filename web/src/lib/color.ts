// Dominant colour of a cover, for the Now Playing background tint.

export function dominantFromPixels(data: ArrayLike<number>): [number, number, number] {
  let r = 0,
    g = 0,
    b = 0,
    wsum = 0;
  for (let i = 0; i + 3 < data.length; i += 4) {
    if (data[i + 3] < 128) continue;
    const R = data[i],
      G = data[i + 1],
      B = data[i + 2];
    const max = Math.max(R, G, B),
      min = Math.min(R, G, B);
    const sat = max === 0 ? 0 : (max - min) / max;
    const lum = (max + min) / 510;
    // Prefer saturated, mid-brightness pixels.
    const w = 0.05 + sat * (1 - Math.abs(lum - 0.5) * 1.6);
    r += R * w;
    g += G * w;
    b += B * w;
    wsum += w;
  }
  if (!wsum) return [40, 40, 40];
  return [Math.round(r / wsum), Math.round(g / wsum), Math.round(b / wsum)];
}

export async function dominantColor(src: string): Promise<[number, number, number] | null> {
  if (typeof document === 'undefined') return null;
  try {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.src = src;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = c.height = 24;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, 24, 24);
    return dominantFromPixels(ctx.getImageData(0, 0, 24, 24).data);
  } catch {
    return null;
  }
}

/** Darken for a background that keeps white text readable. */
export function tint([r, g, b]: [number, number, number], k = 0.45): string {
  return `rgb(${Math.round(r * k)}, ${Math.round(g * k)}, ${Math.round(b * k)})`;
}
