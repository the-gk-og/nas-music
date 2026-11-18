<script lang="ts">
  import { session } from '../lib/session.svelte';
  import { Disc3 } from '@lucide/svelte';

  let {
    albumId,
    size = 256,
    alt = '',
    rounded = 4,
    onsrc,
  }: { albumId: string | null | undefined; size?: number; alt?: string; rounded?: number; onsrc?: (src: string) => void } = $props();

  let src = $state<string | null>(null);
  let failed = $state(false);

  $effect(() => {
    const id = albumId;
    src = null;
    failed = false;
    if (!id) return;
    let live = true;
    session.api
      .artSrc(id, size)
      .then((s) => {
        if (!live) return;
        src = s;
        onsrc?.(s);
      })
      .catch(() => live && (failed = true));
    return () => (live = false);
  });
</script>

<div class="cover" style:border-radius="{rounded}px">
  {#if src && !failed}
    <img {src} {alt} loading="lazy" decoding="async" onerror={() => (failed = true)} />
  {:else}
    <div class="ph"><Disc3 size="40%" strokeWidth={1.2} /></div>
  {/if}
</div>

<style>
  .cover {
    position: relative;
    width: 100%;
    aspect-ratio: 1;
    overflow: hidden;
    background: #181818;
    flex: none;
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .ph {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: #444;
  }
</style>
