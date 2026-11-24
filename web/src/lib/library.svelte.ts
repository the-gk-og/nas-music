// Shared library state for the shell: playlists (sidebar) and pins (Home, "Pin to Home").
import { session } from './session.svelte';
import { folderChoices } from './playlistFolders';
import type { PinType, Playlist, PlaylistFolder, ShelfItem, SmartPlaylist } from './types';
import { ui } from './ui.svelte';

const PIN_TYPES = new Set<string>(['album', 'artist', 'playlist', 'mix', 'queue', 'smart_list', 'smart_playlist', 'genre', 'folder']);

export function isPinType(t: string): t is PinType {
  return PIN_TYPES.has(t);
}

class LibraryStore {
  playlists = $state<Playlist[]>([]);
  /** v10 playlist folders (the user's own); [] on older servers. */
  folders = $state<PlaylistFolder[]>([]);
  /** v7 smart playlists (rule-based); [] on servers without them. */
  smartPlaylists = $state<SmartPlaylist[]>([]);
  pins = $state<ShelfItem[]>([]);
  /** Continue listening items removed on this device ("type:id"), hidden right away. */
  continueDismissed = $state<Set<string>>(new Set());

  async dismissContinue(it: ShelfItem) {
    const type = it.type === 'episode' || it.type === 'book' ? it.type : 'queue';
    const id = type === 'queue' ? (it.progress?.queueId ?? it.id) : it.id;
    this.continueDismissed = new Set([...this.continueDismissed, `${it.type}:${it.id}`]);
    try {
      await session.api.dismissContinue(type, id);
    } catch {
      /* older server: hidden on this device until reload */
    }
  }

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
    await Promise.all([
      session.api.playlists().then((p) => (this.playlists = p), () => {}),
      this.refreshFolders(),
    ]);
  }

  async refreshFolders() {
    try {
      this.folders = await session.api.playlistFolders();
    } catch {
      /* offline, or an older server without folders */
    }
  }

  async newFolder(parentId: string | null = null): Promise<PlaylistFolder | null> {
    const name = await ui.prompt('New folder', 'Name', '', 'Create');
    if (!name) return null;
    try {
      const f = await session.api.createPlaylistFolder(name, parentId);
      await this.refreshFolders();
      return f;
    } catch (e) {
      ui.toast((e as Error).message);
      return null;
    }
  }

  async renameFolder(f: PlaylistFolder) {
    const name = await ui.prompt('Rename folder', 'Name', f.name, 'Save');
    if (!name || name === f.name) return;
    try {
      await session.api.updatePlaylistFolder(f.id, { name });
      await this.refreshFolders();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async deleteFolder(f: PlaylistFolder) {
    if (!(await ui.confirm('Delete folder', `Delete "${f.name}"? Its playlists and folders move up a level; no playlist is deleted.`, 'Delete'))) return;
    try {
      await session.api.deletePlaylistFolder(f.id);
      await this.refreshPlaylists();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  /** Move a folder into another one (or to the top level), picked from a list. */
  async moveFolderTo(f: PlaylistFolder) {
    const choice = await ui.pick(`Move "${f.name}" to`, folderChoices(this.folders, f.id).map((c) => ({ ...c, current: c.value === (f.parentId ?? '') })));
    if (choice === null || choice === (f.parentId ?? '')) return;
    await this.moveFolder(f.id, choice || null);
  }

  async moveFolder(id: string, parentId: string | null) {
    try {
      await session.api.updatePlaylistFolder(id, { parentId });
      await this.refreshFolders();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  /** File a playlist into a folder (null = top level), picked from a list when `folderId` is undefined. */
  async movePlaylist(p: Playlist, folderId?: string | null) {
    if (folderId === undefined) {
      if (!this.folders.length) await this.refreshFolders();
      if (!this.folders.length) {
        const f = await this.newFolder();
        if (!f) return;
        folderId = f.id;
      } else {
        const choice = await ui.pick(`Move "${p.name}" to`, folderChoices(this.folders).map((c) => ({ ...c, current: c.value === (p.folderId ?? '') })));
        if (choice === null) return;
        folderId = choice || null;
      }
    }
    if (folderId === (p.folderId ?? null)) return;
    try {
      await session.api.setPlaylistFolder(p.id, folderId);
      await this.refreshPlaylists();
      const name = this.folders.find((f) => f.id === folderId)?.name;
      ui.toast(name ? `Moved to "${name}"` : 'Moved to the top level');
    } catch (e) {
      ui.toast((e as Error).message);
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
