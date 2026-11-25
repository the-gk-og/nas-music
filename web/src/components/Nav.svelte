<script lang="ts">
  // Navigation for every form (§2, §6): phone bottom tabs, tablet rail, desktop sidebar with playlists.
  import { ChevronDown, ChevronRight, Compass, FolderPlus, House, Library, ListMusic, Plus, Search } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { library } from '../lib/library.svelte';
  import { loadJson, saveJson } from '../lib/platform';
  import { buildTree } from '../lib/playlistFolders';
  import type { PlaylistFolder } from '../lib/types';
  import { session } from '../lib/session.svelte';
  import { TABS, ui, type TabId } from '../lib/ui.svelte';
  import Avatar from './Avatar.svelte';

  const ICONS: Record<TabId, typeof House> = { home: House, foryou: Compass, search: Search, library: Library, queue: ListMusic };
  const form = $derived(appearance.form);
  const tabs = $derived(form === 'desktop' ? TABS.filter((t) => t.id !== 'queue') : TABS);

  // v10 playlist folders: a collapsible tree; drag a playlist or folder onto a folder (or the header) to move it.
  const tree = $derived(buildTree(library.folders, library.playlists));
  let open = $state<Record<string, boolean>>(loadJson('nm.navFolders', {}));
  function toggle(id: string) {
    open = { ...open, [id]: !open[id] };
    saveJson('nm.navFolders', open);
  }
  let dropAt = $state<string | null>(null);
  function dragStart(e: DragEvent, kind: 'playlist' | 'folder', id: string) {
    e.dataTransfer?.setData('text/x-nm-move', `${kind}:${id}`);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }
  function dragOver(e: DragEvent, target: string) {
    if (!e.dataTransfer?.types.includes('text/x-nm-move')) return;
    e.preventDefault();
    dropAt = target;
  }
  async function drop(e: DragEvent, folderId: string | null) {
    e.preventDefault();
    dropAt = null;
    const [kind, id] = (e.dataTransfer?.getData('text/x-nm-move') ?? '').split(':');
    if (kind === 'playlist') {
      const p = library.playlists.find((x) => x.id === id);
      if (p) await library.movePlaylist(p, folderId);
    } else if (kind === 'folder' && id && id !== folderId) {
      await library.moveFolder(id, folderId);
    }
  }
  function folderMenu(e: MouseEvent, f: PlaylistFolder) {
    e.preventDefault();
    void (async () => {
      const act = await ui.pick(f.name, [
        { value: 'playlist', label: 'New playlist here' },
        { value: 'sub', label: 'New folder inside' },
        { value: 'rename', label: 'Rename' },
        { value: 'move', label: 'Move to…' },
        { value: 'delete', label: 'Delete folder' },
      ]);
      if (act === 'playlist') await newPlaylist(f.id);
      if (act === 'sub') await library.newFolder(f.id);
      if (act === 'rename') await library.renameFolder(f);
      if (act === 'move') await library.moveFolderTo(f);
      if (act === 'delete') await library.deleteFolder(f);
    })();
  }

  async function newPlaylist(folderId: string | null = null) {
    const name = await ui.prompt('New playlist', 'Name', '', 'Create');
    if (!name) return;
    try {
      const p = await session.api.createPlaylist(name, folderId);
      await library.refreshPlaylists();
      ui.go('playlist', p.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<nav class="nav {form}" aria-label="Main">
  {#if form === 'desktop'}<div class="brand"><span class="ring"></span>Homebeat</div>{/if}
  <div class="dests">
    {#each tabs as t (t.id)}
      {@const Icon = ICONS[t.id]}
      <button
        class="tab"
        class:active={ui.tab === t.id}
        aria-current={ui.tab === t.id ? 'page' : undefined}
        aria-label={t.label}
        onclick={() => (t.id === 'search' ? ui.focusSearch() : ui.go(t.id))}
      >
        <Icon size={form === 'desktop' ? 22 : 24} strokeWidth={ui.tab === t.id ? 2.5 : 1.9} />
        <span class="label">{t.label}</span>
      </button>
    {/each}
  </div>
  {#if form === 'desktop'}
    <div class="lists">
      <div class="lh">
        <span class:drop={dropAt === ''} role="presentation" ondragover={(e) => dragOver(e, '')} ondragleave={() => (dropAt = null)} ondrop={(e) => drop(e, null)}>Your playlists</span>
        <span>
          <button class="icon-btn sm" aria-label="New folder" title="New folder" onclick={() => library.newFolder()}><FolderPlus size={18} /></button>
          <button class="icon-btn sm" aria-label="New playlist" title="New playlist" onclick={() => newPlaylist()}><Plus size={18} /></button>
        </span>
      </div>
      <div class="scroll">
        <button class="pl" class:active={ui.route.name === 'list' && ui.route.param === 'favourites'} onclick={() => ui.go('list', 'favourites')}>Favorites</button>
        {#snippet playlist(p: import('../lib/types').Playlist, depth: number)}
          <button class="pl ellipsis" style:padding-left="{10 + depth * 16}px" draggable="true" ondragstart={(e) => dragStart(e, 'playlist', p.id)} class:active={ui.route.name === 'playlist' && ui.route.param === p.id} onclick={() => ui.go('playlist', p.id)}>{p.name}</button>
        {/snippet}
        {#snippet folder(n: import('../lib/playlistFolders').FolderNode)}
          <button
            class="pl fo ellipsis"
            class:drop={dropAt === n.folder.id}
            style:padding-left="{4 + n.depth * 16}px"
            draggable="true"
            aria-expanded={!!open[n.folder.id]}
            title="Right-click for folder options"
            ondragstart={(e) => dragStart(e, 'folder', n.folder.id)}
            ondragover={(e) => dragOver(e, n.folder.id)}
            ondragleave={() => (dropAt = null)}
            ondrop={(e) => drop(e, n.folder.id)}
            oncontextmenu={(e) => folderMenu(e, n.folder)}
            onclick={() => toggle(n.folder.id)}
          >
            {#if open[n.folder.id]}<ChevronDown size={16} />{:else}<ChevronRight size={16} />{/if}{n.folder.name}
          </button>
          {#if open[n.folder.id]}
            {#each n.children as c (c.folder.id)}{@render folder(c)}{/each}
            {#each n.playlists as p (p.id)}{@render playlist(p, n.depth + 1)}{/each}
          {/if}
        {/snippet}
        {#each tree.roots as n (n.folder.id)}{@render folder(n)}{/each}
        {#each tree.loose as p (p.id)}{@render playlist(p, 0)}{/each}
      </div>
    </div>
  {/if}
  {#if form !== 'phone'}
    <button class="me" aria-label="Profile and settings" onclick={() => ui.open({ kind: 'profile' })}>
      <Avatar name={session.saved.username ?? '?'} />
      {#if form === 'desktop'}<span class="ellipsis">{session.saved.username}</span>{/if}
    </button>
  {/if}
</nav>

<style>
  .nav {
    background: var(--bg);
    display: flex;
  }
  .tab {
    display: grid;
    place-items: center;
    color: #9a9a9a;
    position: relative;
  }
  .tab.active {
    color: #fff;
  }
  .label {
    font-size: 0.72rem;
  }

  /* phone: bottom tabs */
  .phone {
    border-top: 1px solid var(--line);
    height: calc(var(--tabbar-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
  }
  .phone .dests {
    display: flex;
    flex: 1;
  }
  .phone .tab {
    flex: 1;
    gap: 2px;
    align-content: center;
  }
  .phone .tab.active::before {
    content: '';
    position: absolute;
    top: -1px;
    left: 25%;
    right: 25%;
    height: 3px;
    border-radius: 0 0 3px 3px;
    background: var(--accent);
  }

  /* tablet: navigation rail */
  .tablet {
    flex-direction: column;
    width: 88px;
    border-right: 1px solid var(--line);
    padding: 16px 0;
    align-items: center;
  }
  .tablet .dests {
    display: grid;
    gap: 10px;
    flex: 1;
    /* don't stretch the rows over a tall window (icon and label drifted apart) */
    align-content: start;
  }
  .tablet .tab {
    width: 72px;
    padding: 6px 0;
    gap: 4px;
    align-content: center;
  }
  .tablet .tab :global(svg) {
    padding: 4px 16px;
    box-sizing: content-box;
    border-radius: 16px;
  }
  .tablet .tab.active :global(svg) {
    background: color-mix(in srgb, var(--accent) 25%, transparent);
    color: var(--accent);
  }

  /* desktop: sidebar */
  .desktop {
    flex-direction: column;
    width: var(--sidebar-w);
    padding: 12px 8px;
    gap: 8px;
    border-right: 1px solid var(--line);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 800;
    font-size: 1.2rem;
    padding: 8px 14px 12px;
  }
  .ring {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 3.5px solid var(--accent);
  }
  .desktop .dests {
    display: grid;
    gap: 2px;
    background: var(--bg-1);
    border-radius: 10px;
    padding: 6px;
  }
  .desktop .tab {
    display: flex;
    justify-content: flex-start;
    gap: 16px;
    padding: 10px 12px;
    border-radius: 8px;
    font-weight: 600;
  }
  .desktop .tab:hover {
    color: #fff;
  }
  .desktop .tab.active {
    background: rgba(255, 255, 255, 0.07);
  }
  .desktop .label {
    font-size: 0.98rem;
  }
  .lists {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--bg-1);
    border-radius: 10px;
    padding: 8px 6px;
  }
  .lh {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--fg-3);
    font-weight: 700;
    padding: 0 8px;
  }
  .sm {
    width: 32px;
    height: 32px;
  }
  .scroll {
    overflow-y: auto;
    display: grid;
    align-content: start;
  }
  .pl {
    text-align: left;
    padding: 8px 10px;
    border-radius: 6px;
    color: var(--fg-2);
  }
  .pl:hover {
    background: rgba(255, 255, 255, 0.05);
    color: #fff;
  }
  .pl.active {
    color: var(--accent);
  }
  .pl.fo {
    display: flex;
    align-items: center;
    gap: 4px;
    font-weight: 600;
  }
  .pl.drop,
  .lh .drop {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .me {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 8px;
    border-radius: 999px;
    max-width: 100%;
  }
  .me:hover {
    background: rgba(255, 255, 255, 0.06);
  }
</style>
