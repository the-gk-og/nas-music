<script lang="ts">
  import { ArrowLeft, Play } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, Artist, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let artist = $state<(Artist & { albums: Album[] }) | null>(null);
  let error = $state<string | null>(null);
  $effect(() => {
    session.api.artist(id).then((a) => (artist = a)).catch((e) => (error = e.message));
  });

  async function playAll() {
    if (!artist) return;
    const all: Track[] = [];
    for (const a of artist.albums) all.push(...(await session.api.album(a.id)).tracks);
    await player.playList(all, 0, `Artist: ${artist.name}`, { source: 'library', addedFrom: 'artist' });
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>{artist?.name ?? ''}</h1>
    {#if artist}<button class="icon-btn" aria-label="Play all" onclick={playAll}><Play size={24} fill="currentColor" /></button>{/if}
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if artist}
    <div class="grid">
      {#each artist.albums as a (a.id)}
        <button class="card" onclick={() => ui.go('album', a.id)}>
          <Cover albumId={a.id} size={256} rounded={0} />
          <div class="t ellipsis">{a.title}</div>
          <div class="y muted">{a.year ?? ''}</div>
        </button>
      {/each}
    </div>
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
    padding: 12px;
  }
  .card {
    border: 1.5px solid #333;
    border-radius: 12px;
    overflow: hidden;
    text-align: left;
  }
  .t {
    padding: 8px 10px 0;
  }
  .y {
    padding: 0 10px 8px;
    font-size: 0.85rem;
  }
</style>
