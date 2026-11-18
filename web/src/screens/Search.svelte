<script lang="ts">
  // Global search (§4): Top result · Songs · Albums · Artists · Playlists · Queues · Not in your library.
  import { Search, X } from '@lucide/svelte';
  import Art from '../components/Art.svelte';
  import Card from '../components/Card.svelte';
  import CatalogRow from '../components/CatalogRow.svelte';
  import Shelf from '../components/Shelf.svelte';
  import TrackList from '../components/TrackList.svelte';
  import { ApiError } from '../lib/api/client';
  import { openItem, playItem } from '../lib/items';
  import { loadJson, saveJson } from '../lib/platform';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { SearchResults, ShelfItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let q = $state(ui.route.param || (typeof sessionStorage !== 'undefined' ? (sessionStorage.getItem('nm.search') ?? '') : ''));
  let res = $state<SearchResults | null>(null);
  let busy = $state(false);
  let showAllSongs = $state(false);
  let input: HTMLInputElement | undefined = $state();
  let recent = $state<string[]>(loadJson('nm.recentSearches', { list: [] as string[] }).list);
  let genres = $state<{ name: string }[]>([]);

  $effect(() => {
    void ui.searchFocus;
    input?.focus();
    input?.select();
  });

  $effect(() => {
    session.api.genres().then((g) => (genres = g.map((x) => (typeof x === 'string' ? { name: x } : x)))).catch(() => {});
  });

  async function run(term: string): Promise<SearchResults> {
    try {
      return await session.api.globalSearch(term, 20);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 404) throw e;
      const r = await session.api.search(term, 50); // server without /search: library only
      return { ...r, playlists: [], catalog: [] };
    }
  }

  $effect(() => {
    const term = q.trim();
    try {
      sessionStorage.setItem('nm.search', q);
    } catch {
      /* ignore */
    }
    showAllSongs = false;
    if (!term) {
      res = null;
      return;
    }
    busy = true;
    const h = setTimeout(() => {
      run(term)
        .then((r) => {
          if (q.trim() === term) res = r;
        })
        .catch((e) => ui.toast(e.message))
        .finally(() => (busy = false));
    }, 220);
    return () => clearTimeout(h);
  });

  function remember() {
    const term = q.trim();
    if (!term) return;
    recent = [term, ...recent.filter((x) => x !== term)].slice(0, 8);
    saveJson('nm.recentSearches', { list: recent });
  }

  const meta = $derived({ source: 'search' as const, addedFrom: 'search', searchQuery: q.trim() });
  const albums = $derived((res?.albums ?? []).map((a): ShelfItem => ({ type: 'album', id: a.id, title: a.title, subtitle: `${a.year ?? ''}${a.year ? ' · ' : ''}${a.artist}`, albumId: a.id })));
  const artists = $derived((res?.artists ?? []).map((a): ShelfItem => ({ type: 'artist', id: a.id, title: a.name, subtitle: 'Artist', albumId: null })));
  const playlists = $derived((res?.playlists ?? []).map((p): ShelfItem => ({ type: 'playlist', id: p.id, title: p.name, subtitle: `Playlist · ${p.trackCount ?? 0} songs` })));
  const top = $derived(res?.top ?? albums[0] ?? artists[0] ?? null);
  const empty = $derived(!!res && !res.tracks.length && !albums.length && !artists.length && !playlists.length && !(res.queues?.length) && !res.catalog.length);
</script>

<div class="page search">
  <div class="box">
    <Search size={22} />
    <input
      bind:this={input}
      bind:value={q}
      type="search"
      placeholder="Songs, albums, artists, playlists — and music you don't own"
      aria-label="Search"
      onkeydown={(e) => e.key === 'Enter' && remember()}
      onblur={remember}
    />
    {#if q}<button class="icon-btn" aria-label="Clear" onclick={() => ((q = ''), input?.focus())}><X size={18} /></button>{/if}
  </div>

  {#if !q.trim()}
    {#if recent.length}
      <h2 class="h">Recent searches</h2>
      <div class="chips">{#each recent as r}<button class="chip" onclick={() => (q = r)}>{r}</button>{/each}</div>
    {/if}
    {#if genres.length}
      <h2 class="h">Browse genres</h2>
      <div class="genres">
        {#each genres.slice(0, 24) as g, i (g.name)}
          <button class="genre" style:--h={(i * 47) % 360} onclick={() => ui.go('genre', g.name)}>{g.name}</button>
        {/each}
      </div>
    {/if}
  {:else if !res}
    <div class="empty">{busy ? 'Searching…' : ''}</div>
  {:else if empty}
    <div class="empty">No results for “{q}”.</div>
  {:else}
    <div class="topgrid">
      {#if top}
        <section>
          <h2 class="h">Top result</h2>
          <div class="top">
            <button class="tophit" onclick={() => openItem(top)}>
              <div class="ta"><Art item={top} size={256} round={top.type === 'artist'} /></div>
              <div class="tt">{top.title}</div>
              <div class="muted">{top.subtitle ?? top.type}</div>
            </button>
            {#if top.type !== 'catalog'}<button class="play" aria-label="Play" onclick={() => playItem(top)}>▶</button>{/if}
          </div>
        </section>
      {/if}
      {#if res.tracks.length}
        <section class="songs">
          <h2 class="h">Songs</h2>
          <TrackList tracks={showAllSongs ? res.tracks : res.tracks.slice(0, 4)} queueName="Search: {q.trim()}" {meta} />
          {#if res.tracks.length > 4}<button class="btn text" onclick={() => (showAllSongs = !showAllSongs)}>{showAllSongs ? 'Show less' : `Show all ${res.tracks.length}`}</button>{/if}
        </section>
      {/if}
    </div>
    {#if albums.length}<Shelf title="Albums">{#each albums as it (it.id)}<Card item={it} />{/each}</Shelf>{/if}
    {#if artists.length}<Shelf title="Artists">{#each artists as it (it.id)}<Card item={it} />{/each}</Shelf>{/if}
    {#if playlists.length}<Shelf title="Playlists">{#each playlists as it (it.id)}<Card item={it} />{/each}</Shelf>{/if}
    {#if res.queues?.length}
      <h2 class="h">Queues</h2>
      {#each res.queues as qq (qq.id)}
        <button class="row-item" onclick={() => { ui.go('queue'); void player.showQueue(qq.id); }}>{qq.slot}. {qq.name} <span class="muted">· {qq.trackCount ?? ''} songs</span></button>
      {/each}
    {/if}
    {#if res.catalog.length}
      <h2 class="h">Not in your library</h2>
      <p class="muted note">From an online catalogue — play a 30 s sample or add it to To get.</p>
      {#each res.catalog as c (c.id)}<CatalogRow item={c} />{/each}
    {/if}
  {/if}
</div>

<style>
  .search {
    --gutter: clamp(16px, 3vw, 32px);
  }
  .box {
    position: sticky;
    top: 0;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 12px var(--gutter);
    padding: 4px 8px 4px 16px;
    background: var(--bg-2);
    border-radius: 999px;
    border: 2px solid transparent;
  }
  .box:focus-within {
    border-color: #fff;
  }
  .box input {
    flex: 1;
    background: none;
    border: 0;
    font-size: 1.05rem;
    min-width: 0;
    padding: 10px 0;
  }
  .h {
    margin: 18px var(--gutter) 8px;
    font-size: 1.35rem;
  }
  .note {
    margin: -4px var(--gutter) 6px;
    font-size: 0.85rem;
  }
  .chips {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    padding: 0 var(--gutter);
  }
  .chip {
    border-color: #444;
    padding: 6px 14px;
  }
  .genres {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px;
    padding: 0 var(--gutter);
  }
  .genre {
    height: 90px;
    border-radius: 10px;
    padding: 12px;
    text-align: left;
    font-weight: 800;
    font-size: 1.1rem;
    background: linear-gradient(135deg, hsl(var(--h) 55% 38%), hsl(calc(var(--h) + 40) 50% 20%));
  }
  .topgrid {
    display: grid;
    gap: 8px;
  }
  @media (min-width: 900px) {
    .topgrid {
      grid-template-columns: minmax(280px, 2fr) 3fr;
    }
  }
  .top {
    position: relative;
    margin: 0 var(--gutter);
  }
  .tophit {
    display: block;
    width: 100%;
    text-align: left;
    background: var(--bg-1);
    border-radius: 12px;
    padding: 20px;
  }
  .tophit:hover {
    background: var(--bg-2);
  }
  .ta {
    width: 110px;
  }
  .tt {
    font-size: 1.8rem;
    font-weight: 800;
    margin: 14px 0 4px;
  }
  .play {
    position: absolute;
    right: 20px;
    bottom: 20px;
    width: 50px;
    height: 50px;
    border-radius: 50%;
    background: var(--accent);
    color: #000;
    font-size: 1.2rem;
  }
  .songs .btn {
    margin-left: calc(var(--gutter) - 10px);
  }
</style>
