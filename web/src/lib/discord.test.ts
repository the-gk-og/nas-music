import { describe, expect, it } from 'vitest';
import { changed, presenceFor, validAppId, type PlayerView } from './discord';

const base: PlayerView = {
  track: { title: 'Song', artist: 'Band', album: 'Record' },
  playing: true,
  position: 30,
  duration: 180,
  remote: false,
  speed: 1,
};

describe('Discord status', () => {
  it('validates application ids', () => {
    expect(validAppId('1234567890123456789')).toBe(true);
    expect(validAppId(' 1234567890123456789 ')).toBe(true);
    expect(validAppId('abc')).toBe(false);
    expect(validAppId('')).toBe(false);
  });
  it('shows the song with a progress bar while playing here', () => {
    expect(presenceFor(base, 100_000)).toEqual({ title: 'Song', artist: 'Band', album: 'Record', startedAtMs: 70_000, endsAtMs: 250_000 });
  });
  it('clears when paused, stopped or playing elsewhere', () => {
    expect(presenceFor({ ...base, playing: false }, 0)).toBeNull();
    expect(presenceFor({ ...base, track: null }, 0)).toBeNull();
    expect(presenceFor({ ...base, remote: true }, 0)).toBeNull();
  });
  it('drops the progress bar at other speeds or unknown length', () => {
    expect(presenceFor({ ...base, speed: 1.25 }, 0)?.startedAtMs).toBeNull();
    expect(presenceFor({ ...base, duration: 0 }, 0)?.endsAtMs).toBeNull();
  });
  it('only resends on a new song, play/pause or a seek', () => {
    const a = presenceFor(base, 100_000);
    expect(changed(undefined, a)).toBe(true);
    expect(changed(a, presenceFor({ ...base, position: 31 }, 101_400))).toBe(false); // normal playback drift
    expect(changed(a, presenceFor({ ...base, position: 90 }, 101_000))).toBe(true); // seek
    expect(changed(a, null)).toBe(true);
    expect(changed(null, null)).toBe(false);
    expect(changed(a, presenceFor({ ...base, track: { ...base.track!, title: 'Next' } }, 100_000))).toBe(true);
  });
});
