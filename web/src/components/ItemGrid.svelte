<script lang="ts">
  // Grid or list of items (Library sections), following the section's view setting.
  import { Ellipsis } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { openItem } from '../lib/items';
  import type { ShelfItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Art from './Art.svelte';
  import Card from './Card.svelte';

  let { items, section }: { items: ShelfItem[]; section: string } = $props();
  const view = $derived(appearance.view(section));
</script>

{#if view === 'grid'}
  <div class="grid">{#each items as it (it.type + it.id)}<Card item={it} siblings={items} />{/each}</div>
{:else}
  <div role="list">
    {#each items as it (it.type + it.id)}
      <div class="li" role="listitem">
        <button class="hit" onclick={() => openItem(it, items)} oncontextmenu={(e) => (e.preventDefault(), ui.open({ kind: 'itemMenu', item: it }))}>
          <div class="a"><Art item={it} size={128} round={it.type === 'artist'} /></div>
          <div class="txt"><div class="ellipsis t">{it.title}</div>{#if it.subtitle}<div class="ellipsis s">{it.subtitle}</div>{/if}</div>
        </button>
        <button class="icon-btn" aria-label="More" onclick={() => ui.open({ kind: 'itemMenu', item: it })}><Ellipsis size={20} /></button>
      </div>
    {/each}
  </div>
{/if}
{#if !items.length}<div class="empty">Nothing here</div>{/if}

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 4px;
    padding: 0 calc(var(--gutter, 16px) - 8px);
  }
  .grid :global(.card) {
    --card-w: 100%;
    width: 100%;
  }
  .grid :global(.play) {
    top: auto;
    bottom: 64px;
  }
  @media (max-width: 599px) {
    .grid {
      grid-template-columns: repeat(3, 1fr);
    }
    .grid :global(.s) {
      display: none;
    }
  }
  .li {
    display: flex;
    align-items: center;
    padding: 0 var(--gutter, 16px);
  }
  .li:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  .hit {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 6px 0;
    text-align: left;
  }
  .a {
    width: 52px;
    flex: none;
  }
  .txt {
    min-width: 0;
  }
  .t {
    font-weight: 600;
  }
  .s {
    font-size: 0.85rem;
    color: var(--fg-3);
  }
</style>
