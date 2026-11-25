// Discord "Listening to" status for the desktop app (Rust side: desktop/src-tauri/src/discord.rs). Pure helpers;
// the wiring is in desktop.ts. Off by default; needs the user's Discord application ID.

export interface DiscordPrefs {
  on: boolean;
  appId: string;
}

export const DISCORD_DEFAULTS: DiscordPrefs = { on: false, appId: '' };

/** Discord application IDs are 17–20 digit snowflakes. */
export function validAppId(s: string): boolean {
  return /^\d{17,20}$/.test(s.trim());
}

export interface NowPlaying {
  title: string;
  artist: string;
  album: string;
  startedAtMs: number | null;
  endsAtMs: number | null;
}

export interface PlayerView {
  track: { title: string; artist: string; album: string } | null;
  playing: boolean;
  /** seconds */
  position: number;
  /** seconds */
  duration: number;
  /** Another device is playing (this one is only a remote). */
  remote: boolean;
  /** Playback speed; the progress bar is only shown at normal speed. */
  speed: number;
}

/** What to show, or null to clear the status (paused, stopped, or not playing on this computer). */
export function presenceFor(p: PlayerView, nowMs: number): NowPlaying | null {
  if (!p.track || !p.playing || p.remote) return null;
  const timed = p.duration > 0 && Math.abs(p.speed - 1) < 0.001;
  const start = Math.round(nowMs - p.position * 1000);
  return {
    title: p.track.title,
    artist: p.track.artist,
    album: p.track.album,
    startedAtMs: timed ? start : null,
    endsAtMs: timed ? start + Math.round(p.duration * 1000) : null,
  };
}

/**
 * Whether a new presence differs enough from the last one sent to be worth an update. Discord rate-limits
 * status changes, so the slow drift of a playing song's start time doesn't count; a seek (> 2 s) does.
 */
export function changed(prev: NowPlaying | null | undefined, next: NowPlaying | null): boolean {
  if (prev === undefined) return true;
  if (!prev || !next) return prev !== next;
  if (prev.title !== next.title || prev.artist !== next.artist || prev.album !== next.album) return true;
  if ((prev.startedAtMs == null) !== (next.startedAtMs == null)) return true;
  return prev.startedAtMs != null && next.startedAtMs != null && Math.abs(prev.startedAtMs - next.startedAtMs) > 2000;
}
