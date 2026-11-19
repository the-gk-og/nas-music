<script lang="ts">
  // Weekly album suggestion (§3.2): 30 s preview, Want / Not interested / Have.
  import { Pause, Play } from '@lucide/svelte';
  import { samples } from '../lib/samples.svelte';
  import { session } from '../lib/session.svelte';
  import type { Suggestion } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { s, onchange }: { s: Suggestion; onchange?: (s: Suggestion) => void } = $props();
  let cover = $state<string | null>(null);
  $effect(() => {
    if (s.hasCover) session.api.suggestionCoverSrc(s.id, 256).then((u) => (cover = u)).catch(() => {});
  });

  async function set(status: Suggestion['status']) {
    try {
      await session.api.setSuggestion(s.id, status);
      onchange?.({ ...s, status });
      if (status === 'want') ui.toast('Added to To get');
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="sc" class:dim={s.status === 'not_interested'}>
  <div class="art">
    {#if cover}<img src={cover} alt="" loading="lazy" />{/if}
    {#if s.hasPreview}
      <button class="pv" aria-label={samples.playing === s.id ? 'Stop preview' : 'Play 30-second preview'} onclick={() => samples.toggle(s.id, 'suggestion')}>
        {#if samples.playing === s.id}<Pause size={20} fill="currentColor" />{:else}<Play size={20} fill="currentColor" />{/if}
      </button>
    {/if}
  </div>
  <div class="t ellipsis">{s.album}</div>
  <div class="a ellipsis">{s.artist}{s.year ? ` · ${s.year}` : ''}</div>
  <div class="r">{s.reason}</div>
  <div class="btns">
    <button class:on={s.status === 'want'} onclick={() => set('want')}>Want</button>
    <button class:on={s.status === 'have'} onclick={() => set('have')}>Have</button>
    <button class:on={s.status === 'not_interested'} onclick={() => set('not_interested')} title="Not interested">✕</button>
  </div>
</div>

<style>
  .sc {
    width: 200px;
    flex: none;
    padding: 8px;
  }
  .dim {
    opacity: 0.5;
  }
  .art {
    position: relative;
    aspect-ratio: 1;
    background: #181818;
    border-radius: 8px;
    overflow: hidden;
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .pv {
    position: absolute;
    right: 8px;
    bottom: 8px;
    width: 42px;
    height: 42px;
    border-radius: 50%;
    background: var(--accent);
    color: #000;
    display: grid;
    place-items: center;
  }
  .t {
    font-weight: 600;
    margin-top: 8px;
  }
  .a,
  .r {
    font-size: 0.85rem;
    color: var(--fg-3);
  }
  .r {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    min-height: 2.4em;
  }
  .btns {
    display: flex;
    gap: 6px;
    margin-top: 8px;
  }
  .btns button {
    border: 1px solid #555;
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 0.82rem;
  }
  .btns button.on {
    border-color: var(--accent);
    color: var(--accent);
  }
</style>
