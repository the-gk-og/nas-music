<script lang="ts">
  import { ArrowLeft, Folder, Play } from '@lucide/svelte';
  import TrackList from '../components/TrackList.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { FolderListing } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { path: initial = '', embedded = false }: { path?: string; embedded?: boolean } = $props();
  // Embedded in Library: browse in place; standalone: follow the route.
  let local = $state<string | null>(null);
  const path = $derived(embedded ? (local ?? '') : initial);
  const go = (p: string) => (embedded ? (local = p) : ui.go('folders', p));
  let listing = $state<FolderListing | null>(null);
  let error = $state<string | null>(null);
  $effect(() => {
    listing = null;
    session.api.folders(path).then((l) => (listing = l)).catch((e) => (error = e.message));
  });
  const crumbs = $derived(path ? path.split('/') : []);
</script>

<div class="page">
  <div class="topbar">
    {#if path}<button class="icon-btn" aria-label="Up" onclick={() => go(listing?.parent ?? path.split('/').slice(0, -1).join('/'))}><ArrowLeft size={24} /></button>{/if}
    <div class="crumbs ellipsis">
      <button onclick={() => go('')}>Music</button>
      {#each crumbs as c, i}
        › <button onclick={() => go(crumbs.slice(0, i + 1).join('/'))}>{c}</button>
      {/each}
    </div>
    {#if listing?.tracks.length}
      <button class="icon-btn" aria-label="Play folder" onclick={() => player.playList(listing!.tracks, 0, `Folder: ${crumbs.at(-1) ?? 'Music'}`, { source: 'library', addedFrom: 'folder' })}><Play size={22} fill="currentColor" /></button>
    {/if}
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !listing}<div class="empty">Loading…</div>
  {:else}
    {#each listing.folders as f (f.path)}
      <button class="row-item" onclick={() => go(f.path)}>
        <Folder size={26} fill="currentColor" />
        <div class="ellipsis"><div class="ellipsis">{f.name}</div><div class="sub">{f.trackCount} songs</div></div>
      </button>
    {/each}
    {#if listing.tracks.length}
      <TrackList tracks={listing.tracks} queueName="Folder: {crumbs.at(-1) ?? 'Music'}" meta={{ source: 'library', addedFrom: 'folder' }} />
    {:else if !listing.folders.length}<div class="empty">Empty folder</div>{/if}
  {/if}
</div>

<style>
  .crumbs {
    flex: 1;
    padding: 0 8px;
    color: var(--fg-2);
  }
  .crumbs button:hover {
    text-decoration: underline;
  }
</style>
