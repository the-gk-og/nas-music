<script lang="ts">
  // Spotify-style compact tile for the 2-row "Recently played" grid.
  import { Play } from '@lucide/svelte';
  import { gesture } from '../lib/gesture';
  import { openItem, playItem } from '../lib/items';
  import type { ShelfItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Art from './Art.svelte';

  let { item }: { item: ShelfItem } = $props();
</script>

<div class="tile">
  <button class="hit" use:gesture={{ ontap: () => openItem(item), onlongpress: () => ui.open({ kind: 'itemMenu', item }) }} onkeydown={(e) => e.key === 'Enter' && openItem(item)}>
    <div class="a"><Art {item} size={128} /></div>
    <span class="t">{item.title}</span>
  </button>
  <button class="play" aria-label="Play {item.title}" onclick={() => playItem(item)}><Play size={18} fill="currentColor" /></button>
</div>

<style>
  .tile {
    position: relative;
    background: rgba(255, 255, 255, 0.07);
    border-radius: 6px;
    overflow: hidden;
    transition: background 0.15s;
  }
  .tile:hover {
    background: rgba(255, 255, 255, 0.13);
  }
  .hit {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    text-align: left;
    padding-right: 48px;
  }
  .a {
    width: 56px;
    flex: none;
  }
  .a :global(.art) {
    border-radius: 0;
  }
  .t {
    font-weight: 700;
    font-size: 0.92rem;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .play {
    position: absolute;
    right: 8px;
    top: 50%;
    translate: 0 -50%;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: var(--accent);
    color: #000;
    display: grid;
    place-items: center;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .tile:hover .play,
  .play:focus-visible {
    opacity: 1;
  }
  @media (hover: none) {
    .play {
      display: none;
    }
  }
</style>
