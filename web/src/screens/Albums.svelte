<script lang="ts">
  import { ArrowUpDown, Search, Settings } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import { loadJson, saveJson } from '../lib/platform';
  import { session } from '../lib/session.svelte';
  import type { Album } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  type Sort = 'name' | 'year' | 'added';
  let sort = $state<Sort>(loadJson('nm.albums', { sort: 'name' as Sort }).sort);
  let albums = $state<Album[] | null>(null);
  let q = $state('');
  let sortOpen = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    const s = sort;
    saveJson('nm.albums', { sort: s });
    session.api.albums(s).then((a) => (albums = a)).catch((e) => (error = e.message));
  });

  const shown = $derived(
    (albums ?? []).filter((a) => !q || a.title.toLowerCase().includes(q.toLowerCase()) || a.artist.toLowerCase().includes(q.toLowerCase())),
  );
</script>

<div class="page">
  <div class="topbar">
    <Search size={24} class="muted" />
    <input class="search" placeholder="Search an album…" bind:value={q} aria-label="Search an album" />
    <div class="rel">
      <button class="icon-btn" aria-label="Sort" onclick={() => (sortOpen = !sortOpen)}><ArrowUpDown size={22} /></button>
      {#if sortOpen}
        <div class="menu">
          {#each [['name', 'Name'], ['year', 'Year'], ['added', 'Recently added']] as [k, label]}
            <button class:on={sort === k} onclick={() => ((sort = k as Sort), (sortOpen = false))}>{label}</button>
          {/each}
        </div>
      {/if}
    </div>
    <button class="icon-btn" aria-label="Settings" onclick={() => ui.go('settings')}><Settings size={22} /></button>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !albums}<div class="empty">Loading…</div>
  {:else}
    <div class="grid">
      {#each shown as a (a.id)}
        <button class="card" onclick={() => ui.go('album', a.id)} title="{a.title} — {a.artist}">
          <Cover albumId={a.hasArt === false ? null : a.id} size={256} rounded={0} />
          <div class="t ellipsis">{a.title}</div>
        </button>
      {:else}
        <div class="empty">No albums</div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .search {
    flex: 1;
    background: none;
    border: 0;
    font-size: 1.15rem;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    padding: 12px;
  }
  @media (min-width: 700px) {
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    }
  }
  .card {
    border: 1.5px solid #333;
    border-radius: 12px;
    overflow: hidden;
    text-align: left;
    background: #0d0d0d;
    padding: 0;
  }
  .card:hover {
    border-color: #666;
  }
  .t {
    padding: 8px 10px;
    font-size: 1rem;
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
</style>
