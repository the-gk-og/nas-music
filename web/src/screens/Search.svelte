<script lang="ts">
  import { Disc3, Search, User } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import TrackList from '../components/TrackList.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, Artist, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let q = $state(sessionStorage.getItem('nm.search') ?? '');
  let res = $state<{ artists: Artist[]; albums: Album[]; tracks: Track[] } | null>(null);
  let busy = $state(false);
  let input: HTMLInputElement | undefined = $state();

  $effect(() => {
    input?.focus();
  });

  $effect(() => {
    const term = q.trim();
    sessionStorage.setItem('nm.search', q);
    if (!term) {
      res = null;
      return;
    }
    busy = true;
    const h = setTimeout(() => {
      session.api
        .search(term, 50)
        .then((r) => {
          if (q.trim() === term) res = r;
        })
        .catch((e) => ui.toast(e.message))
        .finally(() => (busy = false));
    }, 220);
    return () => clearTimeout(h);
  });
</script>

<div class="page">
  <div class="topbar">
    <Search size={24} />
    <input bind:this={input} class="search" placeholder="Search songs, albums, artists…" bind:value={q} aria-label="Search" type="search" />
  </div>
  {#if !q.trim()}<div class="empty">Type to search your library.</div>
  {:else if !res}<div class="empty">{busy ? 'Searching…' : ''}</div>
  {:else}
    {#if res.artists.length}
      <div class="section-title">Artists</div>
      {#each res.artists.slice(0, 6) as a (a.id)}
        <button class="row-item" onclick={() => ui.go('artist', a.id)}><User size={24} />{a.name}</button>
      {/each}
    {/if}
    {#if res.albums.length}
      <div class="section-title">Albums</div>
      <div class="albums">
        {#each res.albums.slice(0, 12) as a (a.id)}
          <button class="alb" onclick={() => ui.go('album', a.id)}>
            <Cover albumId={a.id} size={256} />
            <div class="ellipsis">{a.title}</div>
            <div class="ellipsis muted small">{a.artist}</div>
          </button>
        {/each}
      </div>
    {/if}
    {#if res.tracks.length}
      <div class="section-title">Songs</div>
      <TrackList tracks={res.tracks} queueName="Search: {q.trim()}" meta={{ source: 'search', addedFrom: 'search', searchQuery: q.trim() }} />
    {/if}
    {#if !res.artists.length && !res.albums.length && !res.tracks.length}
      <div class="empty"><Disc3 size={32} /><br />No matches for “{q}”.</div>
    {/if}
  {/if}
</div>

<style>
  .search {
    flex: 1;
    background: none;
    border: 0;
    font-size: 1.15rem;
  }
  .albums {
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding: 4px 16px 8px;
  }
  .alb {
    width: 130px;
    flex: none;
    text-align: left;
  }
  .small {
    font-size: 0.85rem;
  }
</style>
