// Shared library state for the shell: playlists (sidebar) and pins (Home, "Pin to Home").
import { session } from './session.svelte';
import type { PinType, Playlist, ShelfItem } from './types';
import { ui } from './ui.svelte';

const PIN_TYPES = new Set<string>(['album', 'artist', 'playlist', 'mix', 'queue', 'smart_list', 'genre', 'folder']);

export function isPinType(t: string): t is PinType {
  return PIN_TYPES.has(t);
}

class LibraryStore {
  playlists = $state<Playlist[]>([]);
  pins = $state<ShelfItem[]>([]);

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

  async togglePin(type: PinType, id: string, title = '') {
    try {
      if (this.isPinned(type, id)) {
        await session.api.unpin(type, id);
        this.pins = this.pins.filter((p) => !(p.type === type && p.id === id));
        ui.toast('Unpinned from Home');
      } else {
        this.pins = await session.api.pin(type, id);
        ui.toast(title ? `Pinned "${title}" to Home` : 'Pinned to Home');
      }
    } catch (e) {
      ui.toast((e as Error).message);
      await this.refreshPins();
    }
  }
}

export const library = new LibraryStore();
