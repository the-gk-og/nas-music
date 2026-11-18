<script lang="ts">
  // A result from the online catalogue: ▶ 30 s sample, Add to To get, Find similar in my library.
  import { Check, Library, Pause, Play, Plus } from '@lucide/svelte';
  import { samples } from '../lib/samples.svelte';
  import { session } from '../lib/session.svelte';
  import type { CatalogItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Art from './Art.svelte';

  let { item }: { item: CatalogItem } = $props();
  let added = $derived(!!item.inToGet);

  async function add() {
    try {
      await session.api.addToGet({ catalogId: item.id, type: item.type === 'track' ? 'track' : 'album', title: item.title, artist: item.artist, year: item.year });
      added = true;
      ui.toast(`Added "${item.title}" to To get`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="row">
  <div class="a"><Art item={{ type: 'catalog', id: item.id, title: item.title, catalog: item }} size={128} /></div>
  <div class="text">
    <div class="ellipsis t">{item.title}</div>
    <div class="ellipsis s">{item.type === 'track' ? 'Song' : item.type === 'album' ? 'Album' : 'Artist'} · {item.artist}{item.year ? ` · ${item.year}` : ''}</div>
    {#if item.owned}<div class="owned">In your library</div>{/if}
  </div>
  {#if item.owned && item.ownedId}
    <button class="btn small" onclick={() => ui.go(item.type === 'artist' ? 'artist' : 'album', item.ownedId!)}><Library size={16} /> Open</button>
  {:else}
    {#if item.hasPreview}
      <button class="icon-btn" aria-label={samples.playing === item.id ? 'Stop sample' : 'Play 30-second sample'} title="30 s sample" onclick={() => samples.toggle(item.id)}>
        {#if samples.playing === item.id}<Pause size={20} />{:else}<Play size={20} />{/if}
      </button>
    {/if}
    <button class="icon-btn" class:on={added} aria-label={added ? 'In To get' : 'Add to To get'} title={added ? 'In To get' : 'Add to To get'} disabled={added} onclick={add}>
      {#if added}<Check size={20} />{:else}<Plus size={20} />{/if}
    </button>
    <button class="icon-btn" aria-label="Find similar in my library" title="Find similar in my library" onclick={() => ui.go('search', item.artist)}><Library size={20} /></button>
  {/if}
</div>

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px var(--gutter, 16px);
  }
  .a {
    width: 52px;
    flex: none;
  }
  .text {
    flex: 1;
    min-width: 0;
  }
  .t {
    font-weight: 600;
  }
  .s {
    color: var(--fg-3);
    font-size: 0.88rem;
  }
  .owned {
    color: var(--accent);
    font-size: 0.8rem;
  }
  .small {
    padding: 6px 10px;
    font-size: 0.85rem;
  }
</style>
