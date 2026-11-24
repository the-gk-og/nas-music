// Admin preview player: one <audio> for listening to library copies side by side (duplicates, cleanup lists).
// Switching to another copy of the same song keeps the position, so A/B comparisons line up. The main player
// is paused while a preview plays.
import { player } from "./player.svelte";

class Preview {
  trackId = $state<string | null>(null);
  playing = $state(false);
  position = $state(0);
  duration = $state(0);
  error = $state<string | null>(null);
  private audio: HTMLAudioElement | null = null;
  private url = "";

  private el(): HTMLAudioElement {
    if (!this.audio) {
      const a = new Audio();
      a.preload = "auto";
      a.addEventListener("play", () => (this.playing = true));
      a.addEventListener("pause", () => (this.playing = false));
      a.addEventListener("ended", () => (this.playing = false));
      a.addEventListener("timeupdate", () => (this.position = a.currentTime));
      a.addEventListener(
        "durationchange",
        () => (this.duration = Number.isFinite(a.duration) ? a.duration : 0),
      );
      a.addEventListener("error", () => {
        this.playing = false;
        this.error = "This browser cannot play that file (try Opus).";
      });
      this.audio = a;
    }
    return this.audio;
  }

  /** Play `url` for `trackId`. keepPosition: continue from where the current copy is (A/B switching). */
  async play(trackId: string, url: string, keepPosition = false) {
    if (!url) {
      this.error = "Previews need a real server.";
      return;
    }
    const a = this.el();
    const at = keepPosition && this.trackId ? a.currentTime : 0;
    this.error = null;
    if (player.playing) player.pause();
    if (this.trackId !== trackId || this.url !== url) {
      a.src = url;
      this.url = url;
      this.trackId = trackId;
      if (at > 0) {
        await new Promise<void>((resolve) => {
          const done = () => resolve();
          a.addEventListener("loadedmetadata", done, { once: true });
          a.addEventListener("error", done, { once: true });
        });
        a.currentTime = Math.min(
          at,
          Number.isFinite(a.duration) ? Math.max(0, a.duration - 1) : at,
        );
      }
    }
    try {
      await a.play();
    } catch (e) {
      if ((e as Error).name !== "AbortError") this.error = (e as Error).message;
    }
  }

  toggle(trackId: string, url: string, keepPosition = false) {
    if (this.trackId === trackId && this.playing) this.stop(false);
    else void this.play(trackId, url, keepPosition);
  }

  seek(s: number) {
    if (this.audio) this.audio.currentTime = s;
  }

  stop(reset = true) {
    this.audio?.pause();
    if (reset && this.audio) {
      this.audio.removeAttribute("src");
      this.url = "";
      this.audio.load();
      this.trackId = null;
      this.position = 0;
    }
  }
}

export const preview = new Preview();
