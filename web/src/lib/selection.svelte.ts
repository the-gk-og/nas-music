// Multi-select: Ctrl/⌘-click, Shift-click or long-press songs, then act on them all from the selection bar.
import { addRange, toggleTrack } from './tracksel';
import type { Track } from './types';

class SelectionStore {
  tracks = $state<Track[]>([]);
  /** Last row clicked, for Shift-click ranges (same list only). */
  private anchor: { list: Track[]; index: number } | null = null;

  get active(): boolean {
    return this.tracks.length > 0;
  }

  has(id: string): boolean {
    return this.tracks.some((t) => t.id === id);
  }

  /** Toggle one row; with `range` (Shift) add everything from the last clicked row in the same list. */
  pick(list: Track[], index: number, range = false) {
    const t = list[index];
    if (!t) return;
    if (range && this.anchor && this.anchor.list === list) this.tracks = addRange(this.tracks, list, this.anchor.index, index);
    else this.tracks = toggleTrack(this.tracks, t);
    this.anchor = { list, index };
  }

  selectAll(list: Track[]) {
    this.tracks = addRange(this.tracks, list, 0, list.length - 1);
  }

  clear() {
    this.tracks = [];
    this.anchor = null;
  }
}

export const selection = new SelectionStore();
