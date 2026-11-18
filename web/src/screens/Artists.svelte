<script lang="ts">
  import { Search, User } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { Artist } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let artists = $state<Artist[] | null>(null);
  let q = $state('');
  let error = $state<string | null>(null);
  $effect(() => {
    session.api.artists().then((a) => (artists = a)).catch((e) => (error = e.message));
  });
  const shown = $derived((artists ?? []).filter((a) => !q || a.name.toLowerCase().includes(q.toLowerCase())));
</script>

<div class="page">
  <div class="topbar">
    <Search size={24} />
    <input class="search" placeholder="Search an artist…" bind:value={q} aria-label="Search an artist" />
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !artists}<div class="empty">Loading…</div>
  {:else}
    {#each shown as a (a.id)}
      <button class="row-item" onclick={() => ui.go('artist', a.id)}>
        <User size={26} />
        <div><div>{a.name}</div><div class="sub">{a.albumCount} album{a.albumCount === 1 ? '' : 's'}</div></div>
      </button>
    {:else}<div class="empty">No artists</div>{/each}
  {/if}
</div>

<style>
  .search {
    flex: 1;
    background: none;
    border: 0;
    font-size: 1.15rem;
  }
</style>
