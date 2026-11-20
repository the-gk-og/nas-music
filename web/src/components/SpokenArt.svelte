<script lang="ts">
  // Cover for a podcast / audiobook / station (server-cached), or an icon when there is none.
  import { BookOpen, Podcast, Radio } from '@lucide/svelte';

  let { src, kind, size = 56, round = false }: { src: string; kind: 'podcast' | 'book' | 'station'; size?: number; round?: boolean } = $props();
  let failed = $state(false);
  const Icon = $derived(kind === 'book' ? BookOpen : kind === 'station' ? Radio : Podcast);
</script>

<div class="sa" class:round style:width="{size}px" style:height="{size}px">
  {#if src && !failed}
    <img {src} alt="" loading="lazy" onerror={() => (failed = true)} />
  {:else}
    <Icon size={Math.round(size * 0.45)} strokeWidth={1.4} />
  {/if}
</div>

<style>
  .sa {
    flex: none;
    border-radius: 6px;
    overflow: hidden;
    background: var(--card, #222);
    display: grid;
    place-items: center;
    color: #8a8a8a;
  }
  .sa.round {
    border-radius: 50%;
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
</style>
