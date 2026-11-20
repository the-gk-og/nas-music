import { describe, expect, it } from 'vitest';
import { ProgressOutbox, chapterAt, clock, partAt, remaining, type SpokenProgress } from './spoken';

describe('spoken helpers', () => {
  const parts = [
    { startS: 0, durationS: 100 },
    { startS: 100, durationS: 50 },
    { startS: 150, durationS: 30 },
  ];

  it('maps book-wide positions to parts', () => {
    expect(partAt(parts, 0)).toEqual({ index: 0, offset: 0 });
    expect(partAt(parts, 120)).toEqual({ index: 1, offset: 20 });
    expect(partAt(parts, 999)).toEqual({ index: 2, offset: 30 });
    expect(partAt([], 5)).toEqual({ index: 0, offset: 0 });
  });

  it('finds the chapter playing', () => {
    expect(chapterAt([0, 60, 120], 59)).toBe(0);
    expect(chapterAt([0, 60, 120], 60)).toBe(1);
    expect(chapterAt([10, 60], 5)).toBe(-1);
  });

  it('formats times', () => {
    expect(clock(3723)).toBe('1:02:03');
    expect(clock(65)).toBe('1:05');
    expect(clock(null)).toBe('–:––');
    const p: SpokenProgress = { kind: 'episode', id: 'e', positionS: 600, durationS: 4200, played: false, speed: null, updatedAt: '' };
    expect(remaining(p, 4200)).toBe('1 h 0 min left');
    expect(remaining(null, 300)).toBe('5 min');
    expect(remaining({ ...p, played: true }, 300)).toBe('Played');
  });

  it('coalesces progress writes per item and keeps newer ones on restore', () => {
    const o = new ProgressOutbox();
    o.add({ kind: 'episode', id: 'a', positionS: 10, updatedAt: '2026-01-01T00:00:01Z' });
    o.add({ kind: 'episode', id: 'a', positionS: 20, updatedAt: '2026-01-01T00:00:02Z' });
    o.add({ kind: 'book', id: 'a', positionS: 5, updatedAt: '2026-01-01T00:00:02Z' });
    const batch = o.take();
    expect(batch).toHaveLength(2);
    expect(batch.find((w) => w.kind === 'episode')?.positionS).toBe(20);
    o.add({ kind: 'episode', id: 'a', positionS: 30, updatedAt: '2026-01-01T00:00:03Z' });
    o.restore(batch); // the failed send must not overwrite the newer position
    expect(o.items.get('episode:a')?.positionS).toBe(30);
    expect(o.items.get('book:a')?.positionS).toBe(5);
    expect(ProgressOutbox.from(JSON.parse(JSON.stringify(o.toJSON()))).items.size).toBe(2);
  });
});
