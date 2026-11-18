<script lang="ts">
  // Cover for any item: album art, a 2×2 collage (mixes), or a catalog cover (music you don't own).
  import { Disc3, Radio, User } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { ShelfItem } from '../lib/types';
  import Cover from './Cover.svelte';

  let { item, size = 256, round = false }: { item: ShelfItem; size?: number; round?: boolean } = $props();
  let catSrc = $state<string | null>(null);

  $effect(() => {
    catSrc = null;
    const c = item.catalog;
    if (item.type !== 'catalog' || !c?.hasCover) return;
    let live = true;
    session.api.catalogCoverSrc(c.id, size).then((s) => live && (catSrc = s)).catch(() => {});
    return () => (live = false);
  });
</script>

<div class="art" class:round>
  {#if item.type === 'catalog'}
    {#if catSrc}<img src={catSrc} alt="" loading="lazy" />{:else}<div class="ph"><Disc3 size="40%" strokeWidth={1.2} /></div>{/if}
  {:else if item.artIds && item.artIds.length >= 4}
    <div class="collage">{#each item.artIds.slice(0, 4) as a}<Cover albumId={a} size={Math.round(size / 2)} rounded={0} />{/each}</div>
  {:else if item.albumId || item.artIds?.length}
    <Cover albumId={item.albumId ?? item.artIds![0]} {size} rounded={0} />
  {:else}
    <div class="ph">{#if item.type === 'artist'}<User size="40%" />{:else if item.type === 'radio'}<Radio size="40%" />{:else}<Disc3 size="40%" strokeWidth={1.2} />{/if}</div>
  {/if}
  {#if item.type === 'radio'}<span class="badge"><Radio size={14} /></span>{/if}
</div>

<style>
  .art {
    position: relative;
    aspect-ratio: 1;
    overflow: hidden;
    border-radius: 8px;
    background: #181818;
  }
  .art.round {
    border-radius: 50%;
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .collage {
    display: grid;
    grid-template-columns: 1fr 1fr;
    width: 100%;
    height: 100%;
  }
  .ph {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: #444;
  }
  .badge {
    position: absolute;
    left: 8px;
    top: 8px;
    background: rgba(0, 0, 0, 0.7);
    border-radius: 999px;
    padding: 4px 6px;
    color: var(--accent);
    display: flex;
  }
</style>
