<script lang="ts">
  // Library (§3.3): chips Playlists · Albums · Artists · Genres · Folders · Songs; sort; grid/list per chip.
  import { ArrowUpDown, Ban, ChevronRight, ClockPlus, Ellipsis, Flame, Folder, FolderPlus, Heart, History, Plus, Sparkles, Tag } from '@lucide/svelte';
  import ItemGrid from '../components/ItemGrid.svelte';
  import TopBar from '../components/TopBar.svelte';
  import TrackList from '../components/TrackList.svelte';
  import ViewToggle from '../components/ViewToggle.svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { library } from '../lib/library.svelte';
  import { loadJson, saveJson } from '../lib/platform';
  import { session } from '../lib/session.svelte';
  import { buildTree, folderPath } from '../lib/playlistFolders';
  import type { Album, Artist, Composer, PlaylistFolder, ShelfItem, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Folders from './Folders.svelte';

  type Chip = 'playlists' | 'albums' | 'artists' | 'composers' | 'genres' | 'folders' | 'songs';
  const ALL_CHIPS: [Chip, string][] = [
    ['playlists', 'Playlists'],
    ['albums', 'Albums'],
    ['artists', 'Artists'],
    ['composers', 'Composers'],
    ['genres', 'Genres'],
    ['folders', 'Folders'],
    ['songs', 'Songs'],
  ];
  // v9: Composers only in classical mode
  const CHIPS = $derived(ALL_CHIPS.filter(([c]) => c !== 'composers' || appearance.prefs.classical));
  const SORTS: Record<Chip, [string, string][]> = {
    playlists: [['recent', 'Recently updated'], ['name', 'Name']],
    albums: [['name', 'Name'], ['year', 'Year'], ['added', 'Recently added'], ['artist', 'Artist']],
    artists: [['name', 'Name'], ['albums', 'Most albums']],
    composers: [['name', 'Name'], ['works', 'Most works']],
    genres: [['name', 'Name'], ['count', 'Most songs']],
    folders: [],
    songs: [['title', 'Title'], ['added', 'Recently added'], ['year', 'Year']],
  };
  const SMART = [
    { k: 'favourites', label: 'Favorites', icon: Heart },
    { k: 'recently_added', label: 'Recently added', icon: ClockPlus },
    { k: 'recently_played', label: 'Recently played', icon: History },
    { k: 'most_played', label: 'Most played', icon: Flame },
    { k: 'not_played', label: 'Not played', icon: Ban },
  ];

  const chip = $derived<Chip>((CHIPS.some(([c]) => c === ui.route.param) ? ui.route.param : loadJson('nm.libChip', { c: 'playlists' }).c) as Chip);
  let sorts = $state<Record<string, string>>(loadJson('nm.libSort', {}));
  const sort = $derived(sorts[chip] ?? SORTS[chip][0]?.[0]);
  let sortOpen = $state(false);

  let albums = $state<Album[] | null>(null);
  let artists = $state<Artist[] | null>(null);
  let genres = $state<{ name: string; trackCount?: number }[] | null>(null);
  let songs = $state<Track[] | null>(null);
  let composers = $state<Composer[] | null>(null);
  /** v9: the playlist folder being browsed (null = top level). */
  let inFolder = $state<string | null>(null);

  $effect(() => {
    saveJson('nm.libChip', { c: chip });
    if (chip === 'playlists') {
      void library.refreshPlaylists();
      void library.refreshSmart();
    }
    if (chip === 'albums') session.api.albums(sort === 'artist' ? 'name' : (sort as 'name')).then((a) => (albums = a)).catch((e) => ui.toast(e.message));
    if (chip === 'artists' && !artists) session.api.artists().then((a) => (artists = a)).catch((e) => ui.toast(e.message));
    if (chip === 'genres' && !genres) session.api.genres().then((g) => (genres = g.map((x) => (typeof x === 'string' ? { name: x } : x)))).catch((e) => ui.toast(e.message));
    if (chip === 'composers' && !composers) session.api.composers().then((c) => (composers = c)).catch((e) => ui.toast(e.message));
    if (chip === 'songs') session.api.allTracks(sort as 'title').then((t) => (songs = t)).catch((e) => ui.toast(e.message));
  });

  function setSort(k: string) {
    sorts = { ...sorts, [chip]: k };
    saveJson('nm.libSort', sorts);
    sortOpen = false;
  }

  const items = $derived.by((): ShelfItem[] => {
    if (chip === 'albums') {
      const a = (albums ?? []).slice();
      if (sort === 'artist') a.sort((x, y) => x.artist.localeCompare(y.artist) || x.title.localeCompare(y.title));
      return a.map((x) => ({ type: 'album', id: x.id, title: x.title, subtitle: `${x.artist}${x.year ? ` · ${x.year}` : ''}`, albumId: x.hasArt === false ? null : x.id }));
    }
    if (chip === 'artists') {
      const a = (artists ?? []).slice();
      if (sort === 'albums') a.sort((x, y) => y.albumCount - x.albumCount);
      return a.map((x) => ({ type: 'artist', id: x.id, title: x.name, subtitle: `${x.albumCount} album${x.albumCount === 1 ? '' : 's'}` }));
    }
    if (chip === 'playlists') {
      const tree = buildTree(library.folders, library.playlists);
      const here = inFolder ? findNode(tree.roots, inFolder) : null;
      const p = (here ? here.playlists : tree.loose).slice();
      if (sort === 'name') p.sort((x, y) => x.name.localeCompare(y.name));
      const smart = inFolder ? [] : library.smartPlaylists.slice();
      if (sort === 'name') smart.sort((x, y) => x.name.localeCompare(y.name));
      return [
        ...smart.map((x): ShelfItem => ({ type: 'smart_playlist', id: x.id, title: x.name, subtitle: `Smart · ${x.trackCount} songs`, smartPlaylist: x })),
        ...p.map((x): ShelfItem => ({ type: 'playlist', id: x.id, title: x.name, subtitle: `${x.trackCount ?? 0} songs` })),
      ];
    }
    return [];
  });
  function findNode(ns: ReturnType<typeof buildTree>['roots'], id: string): ReturnType<typeof buildTree>['roots'][number] | null {
    for (const n of ns) {
      if (n.folder.id === id) return n;
      const f = findNode(n.children, id);
      if (f) return f;
    }
    return null;
  }
  const crumbs = $derived(folderPath(library.folders, inFolder));
  const subfolders = $derived(library.folders.filter((f) => f.parentId === inFolder));
  $effect(() => {
    // the folder was deleted (here or on another device)
    if (inFolder && library.folders.length && !library.folders.some((f) => f.id === inFolder)) inFolder = null;
  });

  async function folderMenu(f: PlaylistFolder) {
    const act = await ui.choose(f.name, [
      { value: 'open', label: 'Open' },
      { value: 'sub', label: 'New folder inside' },
      { value: 'rename', label: 'Rename' },
      { value: 'move', label: 'Move to…' },
      { value: 'delete', label: 'Delete folder' },
    ]);
    if (act === 'open') inFolder = f.id;
    if (act === 'sub') await library.newFolder(f.id);
    if (act === 'rename') await library.renameFolder(f);
    if (act === 'move') await library.moveFolderTo(f);
    if (act === 'delete') await library.deleteFolder(f);
  }
  const composerList = $derived((composers ?? []).slice().sort((a, b) => (sort === 'works' ? b.workCount - a.workCount : a.name.localeCompare(b.name))));
  const genreList = $derived((genres ?? []).slice().sort((a, b) => (sort === 'count' ? (b.trackCount ?? 0) - (a.trackCount ?? 0) : a.name.localeCompare(b.name))));

  async function newPlaylist() {
    const name = await ui.prompt('New playlist', 'Name', '', 'Create');
    if (!name) return;
    try {
      const p = await session.api.createPlaylist(name, inFolder);
      await library.refreshPlaylists();
      ui.go('playlist', p.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="page lib">
  {#if appearance.form === 'desktop'}<TopBar />{/if}
  <h1>Library</h1>
  <div class="bar">
    <div class="chips" role="tablist">
      {#each CHIPS as [c, label]}
        <button class="chip" class:on={chip === c} role="tab" aria-selected={chip === c} onclick={() => ui.go('library', c)}>{label}</button>
      {/each}
      <button class="chip" onclick={() => ui.go('spoken')}>Podcasts &amp; more</button>
    </div>
    <div class="tools">
      {#if SORTS[chip].length}
        <div class="rel">
          <button class="icon-btn" aria-label="Sort" onclick={() => (sortOpen = !sortOpen)}><ArrowUpDown size={20} /></button>
          {#if sortOpen}
            <div class="menu">{#each SORTS[chip] as [k, label]}<button class:on={sort === k} onclick={() => setSort(k)}>{label}</button>{/each}</div>
          {/if}
        </div>
      {/if}
      {#if chip === 'playlists' || chip === 'albums' || chip === 'artists'}<ViewToggle section="lib.{chip}" />{/if}
    </div>
  </div>

  {#if chip === 'playlists'}
    {#if inFolder}
      <nav class="crumbs" aria-label="Folder">
        <button onclick={() => (inFolder = null)}>Playlists</button>
        {#each crumbs as c (c.id)}<ChevronRight size={16} /><button class:here={c.id === inFolder} onclick={() => (inFolder = c.id)}>{c.name}</button>{/each}
      </nav>
    {/if}
    <div class="smart">
      {#each SMART as s (s.k)}
        {@const Icon = s.icon}
        {#if !inFolder}<button class="sm" onclick={() => ui.go('list', s.k)}><Icon size={22} fill={s.k === 'favourites' ? 'currentColor' : 'none'} />{s.label}</button>{/if}
      {/each}
      {#if !inFolder}
        <button class="sm" onclick={() => ui.go('list', 'all')}><Tag size={22} />All songs</button>
        <button class="sm" onclick={() => ui.go('new')}><Sparkles size={22} />New this week</button>
      {/if}
      <button class="sm new" onclick={newPlaylist}><Plus size={22} />New playlist</button>
      <button class="sm new" onclick={() => library.newFolder(inFolder)}><FolderPlus size={22} />New folder</button>
      {#if !inFolder}<button class="sm new" onclick={() => ui.go('smart-edit', '')}><Sparkles size={22} />New smart playlist</button>{/if}
    </div>
    {#if subfolders.length}
      <div class="pfolders">
        {#each subfolders as f (f.id)}
          <div class="pf">
            <button class="pfo" onclick={() => (inFolder = f.id)}><Folder size={22} /><span class="ellipsis">{f.name}</span><span class="muted small">{f.playlistCount}</span></button>
            <button class="icon-btn" aria-label="Folder options" onclick={() => folderMenu(f)}><Ellipsis size={20} /></button>
          </div>
        {/each}
      </div>
    {/if}
    {#if inFolder && !items.length && !subfolders.length}<div class="empty">This folder is empty. Use "Move to folder" on a playlist to file it here.</div>{/if}
    <ItemGrid {items} section="lib.playlists" />
  {:else if chip === 'albums' || chip === 'artists'}
    {#if (chip === 'albums' ? albums : artists) === null}<div class="empty">Loading…</div>{:else}<ItemGrid {items} section="lib.{chip}" />{/if}
  {:else if chip === 'composers'}
    {#if !composers}<div class="empty">Loading…</div>
    {:else if !composers.length}<div class="empty">No works found. Tag tracks with WORK and MOVEMENT, or title them "Work: I. Movement" with a composer.</div>
    {:else}
      <div class="pfolders">
        {#each composerList as c (c.name)}
          <button class="pfo" onclick={() => ui.go('composer', c.name)}><span class="ellipsis">{c.name}</span><span class="muted small">{c.workCount} work{c.workCount === 1 ? '' : 's'}</span></button>
        {/each}
      </div>
    {/if}
  {:else if chip === 'genres'}
    <div class="genres">
      {#each genreList as g, i (g.name)}
        <button class="genre" style:--h={(i * 47) % 360} onclick={() => ui.go('genre', g.name)}>
          {g.name}{#if g.trackCount != null}<span>{g.trackCount} songs</span>{/if}
        </button>
      {/each}
    </div>
  {:else if chip === 'folders'}
    <div class="folders"><Folders embedded /></div>
  {:else if songs}
    <TrackList tracks={songs} queueName="All songs" meta={{ source: 'library', addedFrom: 'songs' }} />
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .crumbs {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    margin: 4px var(--gutter);
    color: var(--fg-2);
  }
  .crumbs button.here {
    color: var(--fg);
    font-weight: 700;
  }
  .pfolders {
    display: grid;
    margin: 4px 0 8px;
  }
  .pf {
    display: flex;
    align-items: center;
    padding-right: var(--gutter);
  }
  .pfo {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 10px var(--gutter);
    text-align: left;
    font-weight: 600;
  }
  .pfo .ellipsis {
    flex: 1;
  }
  .pfo:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  .lib {
    --gutter: clamp(16px, 3vw, 32px);
  }
  h1 {
    margin: 10px var(--gutter) 6px;
    font-size: clamp(1.6rem, 3vw, 2.2rem);
  }
  .bar {
    position: sticky;
    top: 0;
    z-index: 3;
    background: var(--bg);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px var(--gutter);
  }
  .chips {
    flex: 1;
    display: flex;
    gap: 8px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .chip {
    border: 0;
    background: var(--bg-2);
    padding: 7px 14px;
    flex: none;
    font-size: 0.9rem;
  }
  .chip.on {
    background: #fff;
    color: #000;
  }
  .tools {
    display: flex;
    gap: 4px;
    align-items: center;
  }
  .rel {
    position: relative;
  }
  .menu {
    position: absolute;
    right: 0;
    top: 46px;
    z-index: 5;
    background: var(--bg-2);
    border-radius: 10px;
    padding: 6px 0;
    min-width: 180px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.6);
  }
  .menu button {
    display: block;
    width: 100%;
    padding: 10px 16px;
    text-align: left;
  }
  .menu button.on {
    color: var(--accent);
  }
  .smart {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 8px;
    padding: 6px var(--gutter) 14px;
  }
  .sm {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 8px;
    background: var(--bg-1);
    font-weight: 600;
    text-align: left;
  }
  .sm:hover {
    background: var(--bg-2);
  }
  .sm.new {
    color: var(--accent);
    border: 1px dashed #444;
    background: none;
  }
  .genres {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    gap: 12px;
    padding: 4px var(--gutter);
  }
  .genre {
    display: grid;
    align-content: space-between;
    height: 96px;
    border-radius: 10px;
    padding: 12px;
    text-align: left;
    font-weight: 800;
    font-size: 1.05rem;
    background: linear-gradient(135deg, hsl(var(--h) 55% 38%), hsl(calc(var(--h) + 40) 50% 20%));
  }
  .genre span {
    font-weight: 400;
    font-size: 0.8rem;
    opacity: 0.8;
  }
  .folders :global(.page) {
    height: auto;
    overflow: visible;
  }
</style>
