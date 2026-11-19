// One shared player for 30-second catalogue samples (pauses the main player while a sample plays).
import { player } from './player.svelte';
import { session } from './session.svelte';
import { ui } from './ui.svelte';

class Samples {
  playing = $state<string | null>(null);
  private audio: HTMLAudioElement | null = null;
  private resume = false;

  async toggle(catalogId: string, kind: 'catalog' | 'suggestion' = 'catalog') {
    if (this.playing === catalogId) return this.stop();
    this.audio ??= new Audio();
    const a = this.audio;
    a.onended = () => this.stop();
    try {
      a.src = kind === 'catalog' ? await session.api.catalogPreviewSrc(catalogId) : await session.api.suggestionPreviewSrc(catalogId);
      this.resume = player.playing;
      if (player.playing) player.pause();
      await a.play();
      this.playing = catalogId;
    } catch {
      ui.toast('Sample unavailable');
      this.playing = null;
    }
  }

  stop() {
    this.audio?.pause();
    this.playing = null;
    if (this.resume) void player.play();
    this.resume = false;
  }
}

export const samples = new Samples();
