<script lang="ts">
  import { Play } from '@lucide/svelte';
  import { gesture } from '../lib/gesture';
  import { isPlayable, openItem, playItem } from '../lib/items';
  import { progressFraction } from '../lib/v3';
  import type { ShelfItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Art from './Art.svelte';
  import ExplicitBadge from './ExplicitBadge.svelte';

  let { item, siblings, wide = false }: { item: ShelfItem; siblings?: ShelfItem[]; wide?: boolean } = $props();
  const playable = $derived(isPlayable(item));
  const prog = $derived(progressFraction(item.progress));
</script>

<div class="card" class:wide>
  <button
    class="hit"
    aria-label={item.title}
    use:gesture={{ ontap: () => openItem(item, siblings), onlongpress: () => ui.open({ kind: 'itemMenu', item }) }}
    onkeydown={(e) => e.key === 'Enter' && openItem(item, siblings)}
  >
    <Art {item} round={item.type === 'artist'} />
    <div class="t ellipsis"><ExplicitBadge status={item.album?.explicit || item.track?.explicit} />{item.title}</div>
    {#if item.subtitle}<div class="s ellipsis">{item.subtitle}</div>{/if}
    {#if prog != null}<div class="prog"><span style:width="{Math.round(prog * 100)}%"></span></div>{/if}
  </button>
  {#if playable}
    <button class="play" aria-label="Play {item.title}" onclick={() => playItem(item)}><Play size={22} fill="currentColor" /></button>
  {/if}
</div>

<style>
  .card {
    position: relative;
    width: var(--card-w, 168px);
    flex: none;
  }
  .card.wide {
    --card-w: 220px;
  }
  .hit {
    display: block;
    width: 100%;
    text-align: left;
    padding: 8px;
    border-radius: 10px;
    transition: background 0.15s;
  }
  .hit:hover {
    background: rgba(255, 255, 255, 0.06);
  }
  .t {
    margin-top: 8px;
    font-weight: 600;
  }
  .s {
    color: var(--fg-3);
    font-size: 0.88rem;
  }
  .prog {
    height: 3px;
    background: #333;
    border-radius: 2px;
    margin-top: 6px;
  }
  .prog span {
    display: block;
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
  }
  .play {
    position: absolute;
    right: 16px;
    top: calc(var(--card-w, 168px) - 64px);
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: var(--accent);
    color: #000;
    display: grid;
    place-items: center;
    opacity: 0;
    transform: translateY(6px);
    transition:
      opacity 0.15s,
      transform 0.15s;
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.5);
  }
  .card:hover .play,
  .play:focus-visible {
    opacity: 1;
    transform: none;
  }
  @media (hover: none) {
    .play {
      display: none;
    }
  }
</style>
