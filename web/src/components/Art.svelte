<script lang="ts">
  // Cover for any item: its `artUrl` (relative to the API base), a 2×2 collage, or a placeholder.
  import { Blend, Disc3, Radio, Sparkles, User } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { ShelfItem } from '../lib/types';
  import Cover from './Cover.svelte';

  let { item, size = 256, round = false }: { item: ShelfItem; size?: number; round?: boolean } = $props();
  let catSrc = $state<string | null>(null);

  // Album art goes through <Cover> (cached); any other artUrl is resolved here.
  const albumFromUrl = $derived(/^art\/(?!artist\/)([^/?]+)/.exec(item.artUrl ?? '')?.[1] ?? null);
  const rel = $derived(albumFromUrl ? null : (item.artUrl ?? item.catalog?.artUrl ?? item.toget?.artUrl ?? null));
  $effect(() => {
    catSrc = null;
    const r = rel;
    if (!r) return;
    let live = true;
    session.api.relArtSrc(r, size).then((s) => live && (catSrc = s)).catch(() => {});
    return () => (live = false);
  });
</script>

<div class="art" class:round>
  {#if rel}
    {#if catSrc}<img src={catSrc} alt="" loading="lazy" />{:else}<div class="ph"><Disc3 size="40%" strokeWidth={1.2} /></div>{/if}
  {:else if albumFromUrl}
    <Cover albumId={decodeURIComponent(albumFromUrl)} {size} rounded={0} />
  {:else if item.artIds && item.artIds.length >= 4}
    <div class="collage">{#each item.artIds.slice(0, 4) as a}<Cover albumId={a} size={Math.round(size / 2)} rounded={0} />{/each}</div>
  {:else if item.albumId || item.artIds?.length}
    <Cover albumId={item.albumId ?? item.artIds![0]} {size} rounded={0} />
  {:else if item.type === 'wrapped' || item.type === 'blend'}
    <div class="ph grad" class:wr={item.type === 'wrapped'}>
      {#if item.type === 'wrapped'}<Sparkles size="34%" /><span class="yr">{item.wrapped?.year ?? item.id}</span>{:else}<Blend size="40%" />{/if}
    </div>
  {:else}
    <div class="ph">{#if item.type === 'artist'}<User size="40%" />{:else if item.type === 'radio'}<Radio size="40%" />{:else}<Disc3 size="40%" strokeWidth={1.2} />{/if}</div>
  {/if}
  {#if item.type === 'radio'}<span class="badge"><Radio size={14} /></span>{/if}
</div>

<style>
  .grad {
    background: linear-gradient(135deg, var(--accent), #6d28d9);
    color: #fff;
    flex-direction: column;
  }
  .grad.wr {
    background: linear-gradient(135deg, #f97316, #db2777 55%, #6d28d9);
  }
  .yr {
    font-weight: 800;
    font-size: 1.6rem;
  }
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
