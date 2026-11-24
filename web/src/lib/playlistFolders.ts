// v10 playlist folders: pure helpers for the sidebar tree, the Library playlists chip and the "Move to folder" picker.
import type { Playlist, PlaylistFolder } from './types';

export interface FolderNode {
  folder: PlaylistFolder;
  depth: number;
  children: FolderNode[];
  playlists: Playlist[];
}

export interface FolderTree {
  roots: FolderNode[];
  /** Playlists at the top level (no folder, or a folder that no longer exists). */
  loose: Playlist[];
}

/** Build the tree. `folders` comes from the server depth first (parents before children, siblings in order). */
export function buildTree(folders: PlaylistFolder[], playlists: Playlist[]): FolderTree {
  const nodes = new Map<string, FolderNode>();
  const roots: FolderNode[] = [];
  for (const f of folders) nodes.set(f.id, { folder: f, depth: 0, children: [], playlists: [] });
  for (const f of folders) {
    const n = nodes.get(f.id)!;
    const parent = f.parentId ? nodes.get(f.parentId) : undefined;
    if (parent) parent.children.push(n);
    else roots.push(n);
  }
  const setDepth = (ns: FolderNode[], d: number) => {
    for (const n of ns) {
      n.depth = d;
      setDepth(n.children, d + 1);
    }
  };
  setDepth(roots, 0);
  const loose: Playlist[] = [];
  for (const p of playlists) {
    const n = p.folderId ? nodes.get(p.folderId) : undefined;
    if (n) n.playlists.push(p);
    else loose.push(p);
  }
  return { roots, loose };
}

/** The folders from the top down to `id` (for breadcrumbs); [] for the top level or an unknown id. */
export function folderPath(folders: PlaylistFolder[], id: string | null): PlaylistFolder[] {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const out: PlaylistFolder[] = [];
  const seen = new Set<string>();
  for (let f = id ? byId.get(id) : undefined; f && !seen.has(f.id); f = f.parentId ? byId.get(f.parentId) : undefined) {
    seen.add(f.id);
    out.unshift(f);
  }
  return out;
}

/** Choices for "Move to folder": the top level ('') then every folder, indented by depth. Moving a folder
 * excludes itself and everything inside it. */
export function folderChoices(folders: PlaylistFolder[], excluding?: string): { value: string; label: string; depth: number }[] {
  const { roots } = buildTree(folders, []);
  const out: { value: string; label: string; depth: number }[] = [{ value: '', label: 'Top level', depth: 0 }];
  const walk = (ns: FolderNode[]) => {
    for (const n of ns) {
      if (n.folder.id === excluding) continue;
      out.push({ value: n.folder.id, label: n.folder.name, depth: n.depth + 1 });
      walk(n.children);
    }
  };
  walk(roots);
  return out;
}
