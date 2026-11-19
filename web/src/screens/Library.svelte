<script lang="ts">
  // Library (§3.3): chips Playlists · Albums · Artists · Genres · Folders · Songs; sort; grid/list per chip.
  import { ArrowUpDown, Ban, ClockPlus, Flame, Heart, History, Plus, Tag } from '@lucide/svelte';
  import ItemGrid from '../components/ItemGrid.svelte';
  import TopBar from '../components/TopBar.svelte';
  import TrackList from '../components/TrackList.svelte';
  import ViewToggle from '../components/ViewToggle.svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { library } from '../lib/library.svelte';
  import { loadJson, saveJson } from '../lib/platform';
  import { session } from '../lib/session.svelte';
  import type { Album, Artist, ShelfItem, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Folders from './Folders.svelte';

  type Chip = 'playlists' | 'albums' | 'artists' | 'genres' | 'folders' | 'songs';
  const CHIPS: [Chip, string][] = [
    ['playlists', 'Playlists'],
    ['albums', 'Albums'],
    ['artists', 'Artists'],
    ['genres', 'Genres'],
    ['folders', 'Folders'],
    ['songs', 'Songs'],
  ];
  const SORTS: Record<Chip, [string, string][]> = {
    playlists: [['recent', 'Recently updated'], ['name', 'Name']],
    albums: [['name', 'Name'], ['year', 'Year'], ['added', 'Recently added'], ['artist', 'Artist']],
    artists: [['name', 'Name'], ['albums', 'Most albums']],
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

  $effect(() => {
    saveJson('nm.libChip', { c: chip });
    if (chip === 'playlists') void library.refreshPlaylists();
    if (chip === 'albums') session.api.albums(sort === 'artist' ? 'name' : (sort as 'name')).then((a) => (albums = a)).catch((e) => ui.toast(e.message));
    if (chip === 'artists' && !artists) session.api.artists().then((a) => (artists = a)).catch((e) => ui.toast(e.message));
    if (chip === 'genres' && !genres) session.api.genres().then((g) => (genres = g.map((x) => (typeof x === 'string' ? { name: x } : x)))).catch((e) => ui.toast(e.message));
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
      const p = library.playlists.slice();
      if (sort === 'name') p.sort((x, y) => x.name.localeCompare(y.name));
      return p.map((x) => ({ type: 'playlist', id: x.id, title: x.name, subtitle: `${x.trackCount ?? 0} songs` }));
    }
    return [];
  });
  const genreList = $derived((genres ?? []).slice().sort((a, b) => (sort === 'count' ? (b.trackCount ?? 0) - (a.trackCount ?? 0) : a.name.localeCompare(b.name))));

  async function newPlaylist() {
    const name = await ui.prompt('New playlist', 'Name', '', 'Create');
    if (!name) return;
    try {
      const p = await session.api.createPlaylist(name);
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
    <div class="smart">
      {#each SMART as s (s.k)}
        {@const Icon = s.icon}
        <button class="sm" onclick={() => ui.go('list', s.k)}><Icon size={22} fill={s.k === 'favourites' ? 'currentColor' : 'none'} />{s.label}</button>
      {/each}
      <button class="sm" onclick={() => ui.go('list', 'all')}><Tag size={22} />All songs</button>
      <button class="sm new" onclick={newPlaylist}><Plus size={22} />New playlist</button>
    </div>
    <ItemGrid {items} section="lib.playlists" />
  {:else if chip === 'albums' || chip === 'artists'}
    {#if (chip === 'albums' ? albums : artists) === null}<div class="empty">Loading…</div>{:else}<ItemGrid {items} section="lib.{chip}" />{/if}
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
