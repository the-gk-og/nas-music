// Shared library state for the shell: playlists (sidebar), pins (Home), To get ids (search marks).
import { session } from './session.svelte';
import type { Pin, Playlist } from './types';
import { ui } from './ui.svelte';

class LibraryStore {
  playlists = $state<Playlist[]>([]);
  pins = $state<Pin[]>([]);

  async refreshPlaylists() {
    try {
      this.playlists = await session.api.playlists();
    } catch {
      /* offline */
    }
  }

  async refreshPins() {
    try {
      this.pins = await session.api.pins();
    } catch {
      this.pins = [];
    }
  }

  isPinned(type: string, id: string) {
    return this.pins.some((p) => p.type === type && p.id === id);
  }

  async togglePin(type: Pin['type'], id: string, title = '') {
    try {
      if (this.isPinned(type, id)) {
        await session.api.unpin(type, id);
        ui.toast('Unpinned from Home');
      } else {
        await session.api.pin(type, id);
        ui.toast(title ? `Pinned "${title}" to Home` : 'Pinned to Home');
      }
    } catch (e) {
      ui.toast((e as Error).message);
    }
    await this.refreshPins();
  }
}

export const library = new LibraryStore();
