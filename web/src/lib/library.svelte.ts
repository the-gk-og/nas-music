// Shared library state for the shell: playlists (sidebar) and pins (Home, "Pin to Home").
import { session } from './session.svelte';
import type { PinType, Playlist, ShelfItem, SmartPlaylist } from './types';
import { ui } from './ui.svelte';

const PIN_TYPES = new Set<string>(['album', 'artist', 'playlist', 'mix', 'queue', 'smart_list', 'smart_playlist', 'genre', 'folder']);

export function isPinType(t: string): t is PinType {
  return PIN_TYPES.has(t);
}

class LibraryStore {
  playlists = $state<Playlist[]>([]);
  /** v7 smart playlists (rule-based); [] on servers without them. */
  smartPlaylists = $state<SmartPlaylist[]>([]);
  pins = $state<ShelfItem[]>([]);

  /** Live playlist pushes (Connect `playlist`): screens showing that playlist watch `changed`. */
  changed = $state<{ id: string; rev: number | null; deleted: boolean; n: number } | null>(null);
  private changes = 0;

  /** Bumped after an admin tag edit so open views reload. */
  tagsRev = $state(0);

  tagsChanged(oldAlbum: string | null, newAlbum: string | null) {
    this.tagsRev++;
    if (oldAlbum && newAlbum && oldAlbum !== newAlbum && ui.route.name === 'album' && ui.route.param === oldAlbum) {
      ui.go('album', newAlbum);
    }
  }

  playlistChanged(id: string, rev: number | null, deleted: boolean) {
    this.changed = { id, rev, deleted, n: ++this.changes };
    void this.refreshPlaylists();
  }

  async refreshPlaylists() {
    try {
      this.playlists = await session.api.playlists();
    } catch {
      /* offline */
    }
  }

  async refreshSmart() {
    try {
      this.smartPlaylists = await session.api.smartPlaylists();
    } catch {
      this.smartPlaylists = [];
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
